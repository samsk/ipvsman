"""Prometheus/OpenMetrics server."""

from __future__ import annotations

import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from src.state import RuntimeState, read_backend_ip_change_metrics, read_backend_resolve_error_metrics, read_runtime_counters
from src.version import __version__


def wants_openmetrics(accept_header: str | None) -> bool:
    """Return True when client prefers OpenMetrics text format."""
    if not accept_header:
        return False
    return "application/openmetrics-text" in accept_header.lower()


def generate_metrics_body(state: RuntimeState, openmetrics: bool = False) -> tuple[bytes, str]:
    """Build metrics payload and content type.

    Input:
    - state: shared runtime state.
    - openmetrics: when True, emit OpenMetrics text format.

    Output:
    - Tuple (payload bytes, content type header).
    """
    from prometheus_client import CollectorRegistry, generate_latest
    from prometheus_client.core import GaugeMetricFamily

    class Collector:
        def collect(self) -> Any:
            (
                config_version_mtime,
                loaded_files_count,
                desired_generation,
                apply_queue_depth,
                coalesced_drops,
                backend_ip_change_total,
                backend_resolve_error_total,
            ) = read_runtime_counters(state)
            per_frontend_total, per_frontend_last_ts = read_backend_ip_change_metrics(state)
            per_resolve_total, per_resolve_last_ts = read_backend_resolve_error_metrics(state)
            build = GaugeMetricFamily("ipvsman_build_info", "Build info.", labels=["version"])
            build.add_metric([__version__], 1)
            yield build
            yield GaugeMetricFamily(
                "ipvsman_config_version_mtime_seconds",
                "Config version by newest file mtime.",
                value=config_version_mtime,
            )
            yield GaugeMetricFamily(
                "ipvsman_config_files_loaded_total",
                "Loaded config file count.",
                value=loaded_files_count,
            )
            yield GaugeMetricFamily(
                "ipvsman_desired_generation",
                "Desired generation counter.",
                value=desired_generation,
            )
            yield GaugeMetricFamily(
                "ipvsman_apply_queue_depth",
                "Apply queue depth.",
                value=apply_queue_depth,
            )
            yield GaugeMetricFamily(
                "ipvsman_apply_coalesced_drops_total",
                "Apply coalesced drops.",
                value=coalesced_drops,
            )
            yield GaugeMetricFamily(
                "ipvsman_backend_ip_change_events_total",
                "Total backend IP change events across all frontends.",
                value=backend_ip_change_total,
            )
            yield GaugeMetricFamily(
                "ipvsman_backend_resolve_errors_total",
                "Total backend hostname resolve error events across all frontends.",
                value=backend_resolve_error_total,
            )
            per_total = GaugeMetricFamily(
                "ipvsman_backend_ip_change_total",
                "Backend IP change events per frontend.",
                labels=["group", "frontend", "proto", "vip", "port"],
            )
            per_last = GaugeMetricFamily(
                "ipvsman_backend_ip_change_last_timestamp_seconds",
                "Last backend IP change timestamp per frontend.",
                labels=["group", "frontend", "proto", "vip", "port"],
            )
            for key, total in per_frontend_total.items():
                parts = key.split("|", 4)
                if len(parts) != 5:
                    continue
                per_total.add_metric(parts, total)
                per_last.add_metric(parts, per_frontend_last_ts.get(key, 0.0))
            yield per_total
            yield per_last
            per_resolve_count = GaugeMetricFamily(
                "ipvsman_backend_resolve_error_total",
                "Backend hostname resolve error events per frontend.",
                labels=["group", "frontend", "proto", "vip", "port"],
            )
            per_resolve_last = GaugeMetricFamily(
                "ipvsman_backend_resolve_error_last_timestamp_seconds",
                "Last backend hostname resolve error timestamp per frontend.",
                labels=["group", "frontend", "proto", "vip", "port"],
            )
            for key, total in per_resolve_total.items():
                parts = key.split("|", 4)
                if len(parts) != 5:
                    continue
                per_resolve_count.add_metric(parts, total)
                per_resolve_last.add_metric(parts, per_resolve_last_ts.get(key, 0.0))
            yield per_resolve_count
            yield per_resolve_last

    registry = CollectorRegistry(auto_describe=True)
    registry.register(Collector())
    if openmetrics:
        from prometheus_client.openmetrics.exposition import CONTENT_TYPE_LATEST, generate_latest as generate_openmetrics_latest

        return generate_openmetrics_latest(registry), CONTENT_TYPE_LATEST
    return generate_latest(registry), "text/plain; version=0.0.4"


class _MetricsHTTPServer(ThreadingHTTPServer):
    """HTTP server with typed runtime state reference."""

    def __init__(self, server_address: tuple[str, int], RequestHandlerClass: type, state: RuntimeState) -> None:
        super().__init__(server_address, RequestHandlerClass)
        self.state = state


class MetricsServer:
    """Simple metrics server."""

    def __init__(self, state: RuntimeState, host: str, port: int, shutdown_timeout: float = 2.0) -> None:
        self._state = state
        self._host = host
        self._port = port
        self._shutdown_timeout = max(0.1, shutdown_timeout)
        self._http: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start metrics server."""
        state = self._state

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                if self.path != "/metrics":
                    self.send_response(HTTPStatus.NOT_FOUND)
                    self.end_headers()
                    return
                try:
                    body, content_type = generate_metrics_body(
                        self.server.state,
                        openmetrics=wants_openmetrics(self.headers.get("Accept")),
                    )
                except ModuleNotFoundError:
                    msg = b"prometheus_client not installed\n"
                    self.send_response(HTTPStatus.SERVICE_UNAVAILABLE)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.send_header("Content-Length", str(len(msg)))
                    self.end_headers()
                    self.wfile.write(msg)
                    return
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        self._http = _MetricsHTTPServer((self._host, self._port), Handler, state)
        self._thread = threading.Thread(target=self._http.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop metrics server."""
        if self._http is not None:
            self._http.shutdown()
            self._http.server_close()
            self._http = None
        if self._thread is not None:
            self._thread.join(timeout=self._shutdown_timeout)
            self._thread = None
