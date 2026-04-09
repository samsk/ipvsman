"""Prometheus/OpenMetrics server."""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from src import ipvs_exec
from src.constants import HEALTH_HEALTHY, HEALTH_UNKNOWN
from src.state import RuntimeState, read_backend_ip_change_metrics, read_backend_resolve_error_metrics, read_runtime_counters
from src.version import __version__

_IPVS_STATS_CACHE_TTL_SEC = 1.0
_ipvs_stats_cache_lock = threading.Lock()
_ipvs_stats_cache_value: ipvs_exec.IpvsStatsSnapshot | None = None
_ipvs_stats_cache_at: float = 0.0
_ipvs_stats_scrape_failures: int = 0


def wants_openmetrics(accept_header: str | None) -> bool:
    """Return True when client prefers OpenMetrics text format."""
    if not accept_header:
        return False
    return "application/openmetrics-text" in accept_header.lower()


def _read_stats_cached() -> ipvs_exec.IpvsStatsSnapshot:
    """Read IPVS stats with 1-second cache.

    Output:
    - Cached or freshly read IPVS stats snapshot.
    """
    global _ipvs_stats_cache_value, _ipvs_stats_cache_at
    now = time.monotonic()
    with _ipvs_stats_cache_lock:
        if _ipvs_stats_cache_value is not None and (now - _ipvs_stats_cache_at) < _IPVS_STATS_CACHE_TTL_SEC:
            return _ipvs_stats_cache_value
        fresh = ipvs_exec.read_stats()
        _ipvs_stats_cache_value = fresh
        _ipvs_stats_cache_at = now
        return fresh


def _inc_ipvs_stats_scrape_failures() -> None:
    """Increment counter when live IPVS stats read fails."""
    global _ipvs_stats_scrape_failures
    with _ipvs_stats_cache_lock:
        _ipvs_stats_scrape_failures += 1


def _ipvs_stats_scrape_failures_value() -> int:
    """Current IPVS stats scrape failure count."""
    with _ipvs_stats_cache_lock:
        return _ipvs_stats_scrape_failures


def _configured_name_index(state: RuntimeState) -> dict[tuple[str, str, int], tuple[str, str]]:
    """Build route tuple -> configured names index."""
    out: dict[tuple[str, str, int], tuple[str, str]] = {}
    with state.lock:
        snap = state.desired_snapshot or {}
        for grp in snap.get("groups", []):
            for svc in grp.get("services", []):
                key = (
                    str(svc.get("proto", "")),
                    str(svc.get("vip", "")),
                    int(svc.get("port", 0)),
                )
                out[key] = (
                    str(svc.get("group", "unknown")),
                    str(svc.get("frontend_name", "unknown")),
                )
    return out


def _configured_backend_address_index(state: RuntimeState) -> dict[tuple[str, str, str, int], str]:
    """Build configured backend address index keyed by real endpoint."""
    out: dict[tuple[str, str, str, int], str] = {}
    with state.lock:
        snap = state.desired_snapshot or {}
        for grp in snap.get("groups", []):
            for svc in grp.get("services", []):
                group = str(svc.get("group", "unknown"))
                frontend = str(svc.get("frontend_name", "unknown"))
                for rs in svc.get("reals", []):
                    try:
                        key = (group, frontend, str(rs.get("ip", "")), int(rs.get("port", 0)))
                    except Exception:
                        continue
                    out[key] = str(rs.get("address") or rs.get("ip") or "")
    return out


def _frontend_route_index(state: RuntimeState) -> dict[tuple[str, str], list[tuple[str, str, int]]]:
    """Map configured frontend identity to route tuples."""
    out: dict[tuple[str, str], list[tuple[str, str, int]]] = {}
    with state.lock:
        snap = state.desired_snapshot or {}
        for grp in snap.get("groups", []):
            for svc in grp.get("services", []):
                key = (str(svc.get("group", "unknown")), str(svc.get("frontend_name", "unknown")))
                out.setdefault(key, []).append(
                    (str(svc.get("proto", "")), str(svc.get("vip", "")), int(svc.get("port", 0)))
                )
    return out


def _read_health_cache(state: RuntimeState) -> dict[str, Any]:
    """Snapshot health cache atomically."""
    with state.lock:
        return dict(state.health_cache)


def generate_metrics_body(
    state: RuntimeState,
    openmetrics: bool = False,
    include_ipvs_stats: bool = True,
    include_healthchecks: bool = True,
    ipvs_stats_labels_mode: str = "configured",
) -> tuple[bytes, str]:
    """Build metrics payload and content type.

    Input:
    - state: shared runtime state.
    - openmetrics: when True, emit OpenMetrics text format.

    Output:
    - Tuple (payload bytes, content type header).
    """
    from prometheus_client import CollectorRegistry, generate_latest
    from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily

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
                "ipvsman_backend_resolve_error_events_total",
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
                "ipvsman_backend_resolve_errors_total",
                "Backend hostname resolve error events per frontend.",
                labels=["group", "frontend", "proto", "vip", "port"],
            )
            per_resolve_last = GaugeMetricFamily(
                "ipvsman_backend_resolve_errors_last_timestamp_seconds",
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
            if include_healthchecks:
                mode = ipvs_stats_labels_mode
                cache = _read_health_cache(state)
                addr_idx = _configured_backend_address_index(state)
                route_idx = _frontend_route_index(state)
                if mode in {"configured", "both"}:
                    hc_state = GaugeMetricFamily(
                        "ipvsman_healthcheck_state",
                        "Healthcheck state grouped by configured labels (1=healthy, 0=unhealthy, -1=unknown).",
                        labels=["group", "frontend", "address", "backend_port"],
                    )
                    hc_ready = GaugeMetricFamily(
                        "ipvsman_healthcheck_ready",
                        "Healthcheck readiness grouped by configured labels (1/0).",
                        labels=["group", "frontend", "address", "backend_port"],
                    )
                    for key, result in cache.items():
                        parts = key.split("|", 3)
                        if len(parts) != 4:
                            continue
                        group, frontend, backend_ip, backend_port_txt = parts
                        try:
                            backend_port = int(backend_port_txt)
                        except ValueError:
                            continue
                        address = addr_idx.get((group, frontend, backend_ip, backend_port), backend_ip)
                        labels = [group, frontend, address, str(backend_port)]
                        state_val = -1.0 if result.state == HEALTH_UNKNOWN else (1.0 if result.state == HEALTH_HEALTHY else 0.0)
                        hc_state.add_metric(labels, state_val)
                        hc_ready.add_metric(labels, 1.0 if bool(result.ready) else 0.0)
                    yield hc_state
                    yield hc_ready
                if mode in {"route", "both"}:
                    suffix = "_route" if mode == "both" else ""
                    hc_state = GaugeMetricFamily(
                        f"ipvsman_healthcheck_state{suffix}",
                        "Healthcheck state grouped by route labels (1=healthy, 0=unhealthy, -1=unknown).",
                        labels=["proto", "vip", "port", "backend_ip", "backend_port"],
                    )
                    hc_ready = GaugeMetricFamily(
                        f"ipvsman_healthcheck_ready{suffix}",
                        "Healthcheck readiness grouped by route labels (1/0).",
                        labels=["proto", "vip", "port", "backend_ip", "backend_port"],
                    )
                    for key, result in cache.items():
                        parts = key.split("|", 3)
                        if len(parts) != 4:
                            continue
                        group, frontend, backend_ip, backend_port_txt = parts
                        try:
                            backend_port = int(backend_port_txt)
                        except ValueError:
                            continue
                        routes = route_idx.get((group, frontend), [])
                        state_val = -1.0 if result.state == HEALTH_UNKNOWN else (1.0 if result.state == HEALTH_HEALTHY else 0.0)
                        for proto, vip, port in routes:
                            labels = [proto, vip, str(port), backend_ip, str(backend_port)]
                            hc_state.add_metric(labels, state_val)
                            hc_ready.add_metric(labels, 1.0 if bool(result.ready) else 0.0)
                    yield hc_state
                    yield hc_ready
            if include_ipvs_stats:
                stats = None
                try:
                    stats = _read_stats_cached()
                except Exception:
                    _inc_ipvs_stats_scrape_failures()
                fail = CounterMetricFamily(
                    "ipvsman_ipvs_stats_scrape_failures_total",
                    "Total failed attempts to read live IPVS stats for Prometheus.",
                )
                fail.add_metric([], _ipvs_stats_scrape_failures_value())
                yield fail
                if stats is not None:
                    mode = ipvs_stats_labels_mode
                    if mode in {"route", "both"}:
                        route_suffix = "_route" if mode == "both" else ""
                        svc_conns = GaugeMetricFamily(
                            f"ipvsman_ipvs_service_connections{route_suffix}",
                            "Live IPVS service connections.",
                            labels=["proto", "vip", "port"],
                        )
                        svc_inpkts = GaugeMetricFamily(
                            f"ipvsman_ipvs_service_inpkts_total{route_suffix}",
                            "Live IPVS service input packets.",
                            labels=["proto", "vip", "port"],
                        )
                        svc_outpkts = GaugeMetricFamily(
                            f"ipvsman_ipvs_service_outpkts_total{route_suffix}",
                            "Live IPVS service output packets.",
                            labels=["proto", "vip", "port"],
                        )
                        svc_inbytes = GaugeMetricFamily(
                            f"ipvsman_ipvs_service_inbytes_total{route_suffix}",
                            "Live IPVS service input bytes.",
                            labels=["proto", "vip", "port"],
                        )
                        svc_outbytes = GaugeMetricFamily(
                            f"ipvsman_ipvs_service_outbytes_total{route_suffix}",
                            "Live IPVS service output bytes.",
                            labels=["proto", "vip", "port"],
                        )
                        rs_conns = GaugeMetricFamily(
                            f"ipvsman_ipvs_real_connections{route_suffix}",
                            "Live IPVS real server connections.",
                            labels=["proto", "vip", "port", "backend_ip", "backend_port"],
                        )
                        rs_inpkts = GaugeMetricFamily(
                            f"ipvsman_ipvs_real_inpkts_total{route_suffix}",
                            "Live IPVS real server input packets.",
                            labels=["proto", "vip", "port", "backend_ip", "backend_port"],
                        )
                        rs_outpkts = GaugeMetricFamily(
                            f"ipvsman_ipvs_real_outpkts_total{route_suffix}",
                            "Live IPVS real server output packets.",
                            labels=["proto", "vip", "port", "backend_ip", "backend_port"],
                        )
                        rs_inbytes = GaugeMetricFamily(
                            f"ipvsman_ipvs_real_inbytes_total{route_suffix}",
                            "Live IPVS real server input bytes.",
                            labels=["proto", "vip", "port", "backend_ip", "backend_port"],
                        )
                        rs_outbytes = GaugeMetricFamily(
                            f"ipvsman_ipvs_real_outbytes_total{route_suffix}",
                            "Live IPVS real server output bytes.",
                            labels=["proto", "vip", "port", "backend_ip", "backend_port"],
                        )
                        for svc in stats.services:
                            labels = [svc.proto, svc.vip, str(svc.port)]
                            svc_conns.add_metric(labels, svc.conns)
                            svc_inpkts.add_metric(labels, svc.inpkts)
                            svc_outpkts.add_metric(labels, svc.outpkts)
                            svc_inbytes.add_metric(labels, svc.inbytes)
                            svc_outbytes.add_metric(labels, svc.outbytes)
                            for rs in svc.reals:
                                rlabels = labels + [rs.ip, str(rs.port)]
                                rs_conns.add_metric(rlabels, rs.active_conn)
                                rs_inpkts.add_metric(rlabels, rs.inpkts)
                                rs_outpkts.add_metric(rlabels, rs.outpkts)
                                rs_inbytes.add_metric(rlabels, rs.inbytes)
                                rs_outbytes.add_metric(rlabels, rs.outbytes)
                        yield svc_conns
                        yield svc_inpkts
                        yield svc_outpkts
                        yield svc_inbytes
                        yield svc_outbytes
                        yield rs_conns
                        yield rs_inpkts
                        yield rs_outpkts
                        yield rs_inbytes
                        yield rs_outbytes
                    if mode in {"configured", "both"}:
                        idx = _configured_name_index(state)
                        addr_idx = _configured_backend_address_index(state)
                        svc_acc: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0, 0, 0, 0])
                        rs_acc: dict[tuple[str, str, str, int], list[int]] = defaultdict(lambda: [0, 0, 0, 0, 0])
                        for svc in stats.services:
                            grp, fe = idx.get((svc.proto, svc.vip, svc.port), ("unknown", "unknown"))
                            skey = (grp, fe)
                            svc_acc[skey][0] += svc.conns
                            svc_acc[skey][1] += svc.inpkts
                            svc_acc[skey][2] += svc.outpkts
                            svc_acc[skey][3] += svc.inbytes
                            svc_acc[skey][4] += svc.outbytes
                            for rs in svc.reals:
                                address = addr_idx.get((grp, fe, rs.ip, rs.port), rs.ip)
                                rkey = (grp, fe, address, rs.port)
                                rs_acc[rkey][0] += rs.active_conn
                                rs_acc[rkey][1] += rs.inpkts
                                rs_acc[rkey][2] += rs.outpkts
                                rs_acc[rkey][3] += rs.inbytes
                                rs_acc[rkey][4] += rs.outbytes
                        svc_conns = GaugeMetricFamily(
                            "ipvsman_ipvs_service_connections",
                            "Live IPVS service connections grouped by configured names.",
                            labels=["group", "frontend"],
                        )
                        svc_inpkts = GaugeMetricFamily(
                            "ipvsman_ipvs_service_inpkts_total",
                            "Live IPVS service input packets grouped by configured names.",
                            labels=["group", "frontend"],
                        )
                        svc_outpkts = GaugeMetricFamily(
                            "ipvsman_ipvs_service_outpkts_total",
                            "Live IPVS service output packets grouped by configured names.",
                            labels=["group", "frontend"],
                        )
                        svc_inbytes = GaugeMetricFamily(
                            "ipvsman_ipvs_service_inbytes_total",
                            "Live IPVS service input bytes grouped by configured names.",
                            labels=["group", "frontend"],
                        )
                        svc_outbytes = GaugeMetricFamily(
                            "ipvsman_ipvs_service_outbytes_total",
                            "Live IPVS service output bytes grouped by configured names.",
                            labels=["group", "frontend"],
                        )
                        rs_conns = GaugeMetricFamily(
                            "ipvsman_ipvs_real_connections",
                            "Live IPVS real server connections grouped by configured names.",
                            labels=["group", "frontend", "address", "backend_port"],
                        )
                        rs_inpkts = GaugeMetricFamily(
                            "ipvsman_ipvs_real_inpkts_total",
                            "Live IPVS real server input packets grouped by configured names.",
                            labels=["group", "frontend", "address", "backend_port"],
                        )
                        rs_outpkts = GaugeMetricFamily(
                            "ipvsman_ipvs_real_outpkts_total",
                            "Live IPVS real server output packets grouped by configured names.",
                            labels=["group", "frontend", "address", "backend_port"],
                        )
                        rs_inbytes = GaugeMetricFamily(
                            "ipvsman_ipvs_real_inbytes_total",
                            "Live IPVS real server input bytes grouped by configured names.",
                            labels=["group", "frontend", "address", "backend_port"],
                        )
                        rs_outbytes = GaugeMetricFamily(
                            "ipvsman_ipvs_real_outbytes_total",
                            "Live IPVS real server output bytes grouped by configured names.",
                            labels=["group", "frontend", "address", "backend_port"],
                        )
                        for (grp, fe), vals in svc_acc.items():
                            labels = [grp, fe]
                            svc_conns.add_metric(labels, vals[0])
                            svc_inpkts.add_metric(labels, vals[1])
                            svc_outpkts.add_metric(labels, vals[2])
                            svc_inbytes.add_metric(labels, vals[3])
                            svc_outbytes.add_metric(labels, vals[4])
                        for (grp, fe, ip, port), vals in rs_acc.items():
                            labels = [grp, fe, ip, str(port)]
                            rs_conns.add_metric(labels, vals[0])
                            rs_inpkts.add_metric(labels, vals[1])
                            rs_outpkts.add_metric(labels, vals[2])
                            rs_inbytes.add_metric(labels, vals[3])
                            rs_outbytes.add_metric(labels, vals[4])
                        yield svc_conns
                        yield svc_inpkts
                        yield svc_outpkts
                        yield svc_inbytes
                        yield svc_outbytes
                        yield rs_conns
                        yield rs_inpkts
                        yield rs_outpkts
                        yield rs_inbytes
                        yield rs_outbytes

    registry = CollectorRegistry(auto_describe=False)
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

    def __init__(
        self,
        state: RuntimeState,
        host: str,
        port: int,
        shutdown_timeout: float = 2.0,
        include_ipvs_stats: bool = True,
        include_healthchecks: bool = True,
        ipvs_stats_labels_mode: str = "configured",
    ) -> None:
        self._state = state
        self._host = host
        self._port = port
        self._shutdown_timeout = max(0.1, shutdown_timeout)
        self._include_ipvs_stats = include_ipvs_stats
        self._include_healthchecks = include_healthchecks
        self._ipvs_stats_labels_mode = ipvs_stats_labels_mode
        self._http: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start metrics server."""
        state = self._state
        metrics_server = self

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
                        include_ipvs_stats=metrics_server._include_ipvs_stats,
                        include_healthchecks=metrics_server._include_healthchecks,
                        ipvs_stats_labels_mode=metrics_server._ipvs_stats_labels_mode,
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
