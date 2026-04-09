"""HTTP API server."""

from __future__ import annotations

import json
import hmac
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import yaml

try:
    from pydantic import ValidationError
except ModuleNotFoundError:  # pragma: no cover
    ValidationError = ValueError

from src import constants
from src.loader import load_snapshot
from src.list_views import list_backends, list_frontends, list_healthchecks, list_services
from src.manual_checks import run_manual_checks
from src.metrics import generate_metrics_body, wants_openmetrics
from src.models import ApiConfigPut, Group
from src.openapi import openapi_json, openapi_yaml_text
from src.reconcile import build_report
from src.state import RuntimeState, read_desired_snapshot, update_desired_snapshot


def _group_to_yaml_dict(group: Group | dict[str, Any]) -> dict[str, Any]:
    """Serialize validated group for YAML (handles dict or Group)."""
    if isinstance(group, dict):
        return group
    return group.model_dump()


class _IpRateLimiter:
    """Sliding-window per-IP request cap."""

    def __init__(self, max_per_minute: int) -> None:
        self._max = max(1, max_per_minute)
        self._lock = threading.Lock()
        self._hits: dict[str, list[float]] = {}

    def allow(self, client_addr: tuple[str, int]) -> bool:
        """Return True if request is under limit."""
        ip = client_addr[0]
        now = time.time()
        with self._lock:
            bucket = self._hits.setdefault(ip, [])
            cutoff = now - 60.0
            bucket[:] = [t for t in bucket if t > cutoff]
            if len(bucket) >= self._max:
                return False
            bucket.append(now)
            if len(self._hits) > 10000:
                self._prune_stale(now)
        return True

    def _prune_stale(self, now: float) -> None:
        cutoff = now - 60.0
        stale = [k for k, v in self._hits.items() if not v or max(v) < cutoff]
        for k in stale[:1000]:
            del self._hits[k]


class ApiServer:
    """Lightweight API server."""

    def __init__(
        self,
        state: RuntimeState,
        config_dir: Path,
        host: str,
        port: int,
        token: str | None,
        enable_write: bool,
        max_body_bytes: int,
        enable_metrics: bool = False,
        metrics_include_ipvs_stats: bool = True,
        metrics_include_healthchecks: bool = True,
        metrics_ipvs_stats_labels_mode: str = "configured",
        shutdown_timeout: float = 2.0,
        rate_limit_per_minute: int | None = None,
    ) -> None:
        self._state = state
        self._config_dir = config_dir
        self._host = host
        self._port = port
        self._token = token
        self._enable_write = enable_write
        self._max_body_bytes = max_body_bytes
        self._enable_metrics = enable_metrics
        self._metrics_include_ipvs_stats = metrics_include_ipvs_stats
        self._metrics_include_healthchecks = metrics_include_healthchecks
        self._metrics_ipvs_stats_labels_mode = metrics_ipvs_stats_labels_mode
        self._shutdown_timeout = max(0.1, shutdown_timeout)
        self._rate_limit = _IpRateLimiter(rate_limit_per_minute or constants.DEFAULT_API_RATE_LIMIT_PER_MINUTE)
        self._http: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def _auth_ok(self, handler: BaseHTTPRequestHandler) -> bool:
        """Validate bearer auth for API request.

        Inputs:
        - handler: request handler with headers.

        Output:
        - True when request is authorized.
        """
        if not self._token:
            return True
        auth = handler.headers.get("Authorization", "")
        return hmac.compare_digest(auth, f"Bearer {self._token}")

    def start(self) -> None:
        """Start API server."""
        state = self._state
        config_dir = self._config_dir
        server = self

        class Handler(BaseHTTPRequestHandler):
            def _json(self, payload: Any, code: int = 200) -> None:
                body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802
                if not server._rate_limit.allow(self.client_address):
                    self._json({"error": "rate limit"}, 429)
                    return
                if self.path == "/metrics" and server._enable_metrics:
                    if not server._auth_ok(self):
                        self._json({"error": "unauthorized"}, 401)
                        return
                    try:
                        body, content_type = generate_metrics_body(
                            state,
                            openmetrics=wants_openmetrics(self.headers.get("Accept")),
                            include_ipvs_stats=server._metrics_include_ipvs_stats,
                            include_healthchecks=server._metrics_include_healthchecks,
                            ipvs_stats_labels_mode=server._metrics_ipvs_stats_labels_mode,
                        )
                    except ModuleNotFoundError:
                        msg = b"prometheus_client not installed\n"
                        self.send_response(503)
                        self.send_header("Content-Type", "text/plain; charset=utf-8")
                        self.send_header("Content-Length", str(len(msg)))
                        self.end_headers()
                        self.wfile.write(msg)
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if self.path in ("/openapi.json",):
                    if not server._auth_ok(self):
                        self._json({"error": "unauthorized"}, 401)
                        return
                    body = openapi_json()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if self.path in ("/openapi.yaml",):
                    if not server._auth_ok(self):
                        self._json({"error": "unauthorized"}, 401)
                        return
                    body = openapi_yaml_text().encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/yaml")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if not server._auth_ok(self):
                    self._json({"error": "unauthorized"}, 401)
                    return
                snap = read_desired_snapshot(state, deep_copy=True)
                if self.path == "/v1/services":
                    self._json({"services": list_services(snap)})
                    return
                if self.path == "/v1/frontends":
                    self._json({"frontends": list_frontends(snap)})
                    return
                if self.path == "/v1/backends":
                    self._json({"backends": list_backends(snap)})
                    return
                if self.path == "/v1/healthchecks":
                    self._json({"healthchecks": list_healthchecks(snap)})
                    return
                if self.path == "/v1/status/detailed":
                    report = build_report(snap, snap.get("live_state"))
                    self._json(report)
                    return
                self._json({"error": "not found"}, 404)

            def do_POST(self) -> None:  # noqa: N802
                if not server._rate_limit.allow(self.client_address):
                    self._json({"error": "rate limit"}, 429)
                    return
                if self.path != "/v1/healthchecks/run":
                    self._json({"error": "not found"}, 404)
                    return
                if not server._auth_ok(self):
                    self._json({"error": "unauthorized"}, 401)
                    return
                result = run_manual_checks(read_desired_snapshot(state, deep_copy=True), None, None)
                self._json(result)

            def do_PUT(self) -> None:  # noqa: N802
                if not server._rate_limit.allow(self.client_address):
                    self._json({"error": "rate limit"}, 429)
                    return
                if self.path != "/v1/config":
                    self._json({"error": "not found"}, 404)
                    return
                if not server._enable_write:
                    self._json({"error": "write disabled"}, 403)
                    return
                if not server._auth_ok(self):
                    self._json({"error": "unauthorized"}, 401)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    self._json({"error": "invalid content-length"}, 400)
                    return
                if length < 0:
                    self._json({"error": "invalid content-length"}, 400)
                    return
                if length > server._max_body_bytes:
                    self._json({"error": "payload too large"}, 413)
                    return
                body = self.rfile.read(length)
                try:
                    data = json.loads(body.decode("utf-8"))
                    payload = ApiConfigPut.model_validate(data)
                except (json.JSONDecodeError, ValidationError) as exc:
                    self._json({"error": "validation failed", "detail": str(exc)}, 422)
                    return
                target = config_dir / "groups" / "api-put.yaml"
                tmp = target.with_suffix(".yaml.tmp")
                backup = target.with_suffix(".yaml.bak")
                groups_yaml = [_group_to_yaml_dict(g) for g in payload.groups]
                tmp.write_text(yaml.safe_dump(groups_yaml, sort_keys=False), encoding="utf-8")
                had_existing = target.exists()
                if had_existing:
                    target.rename(backup)
                try:
                    tmp.replace(target)
                    next_snapshot = load_snapshot(config_dir)
                except Exception as exc:
                    # Rollback: remove broken file, restore backup
                    target.unlink(missing_ok=True)
                    if had_existing:
                        backup.rename(target)
                    tmp.unlink(missing_ok=True)
                    self._json({"error": "load failed", "detail": str(exc)}, 422)
                    return
                finally:
                    backup.unlink(missing_ok=True)
                update_desired_snapshot(state, next_snapshot, carry_live_state=True)
                self._json({"ok": True})

            def log_message(self, _format: str, *_args: object) -> None:
                return

        self._http = ThreadingHTTPServer((self._host, self._port), Handler)
        self._thread = threading.Thread(target=self._http.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop API server."""
        if self._http is not None:
            self._http.shutdown()
            self._http.server_close()
            self._http = None
        if self._thread is not None:
            self._thread.join(timeout=self._shutdown_timeout)
            self._thread = None
