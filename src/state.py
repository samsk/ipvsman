"""Shared daemon state."""

from __future__ import annotations

import copy
import ipaddress
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from src.constants import HEALTH_UNKNOWN
from src.models import RuntimeCheckResult


@dataclass
class RuntimeState:
    """In-memory runtime state."""

    desired_generation: int = 0
    config_version_mtime: float = 0.0
    loaded_files_count: int = 0
    desired_snapshot: dict[str, Any] = field(default_factory=dict)
    last_applied_snapshot: dict[str, Any] = field(default_factory=dict)
    apply_queue_depth: int = 0
    coalesced_drops: int = 0
    health_cache: dict[str, RuntimeCheckResult] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)
    started_at: float = field(default_factory=time.time)
    last_reload_at: float = 0.0
    last_reload_error: str | None = None
    backend_ip_change_total: int = 0
    backend_ip_change_per_frontend_total: dict[str, int] = field(default_factory=dict)
    backend_ip_change_per_frontend_last_ts: dict[str, float] = field(default_factory=dict)
    backend_resolve_error_total: int = 0
    backend_resolve_error_per_frontend_total: dict[str, int] = field(default_factory=dict)
    backend_resolve_error_per_frontend_last_ts: dict[str, float] = field(default_factory=dict)

    def set_health(self, key: str, result: RuntimeCheckResult) -> None:
        """Store health check result."""
        with self.lock:
            self.health_cache[key] = result

    def get_health(self, key: str) -> RuntimeCheckResult:
        """Return health result or unknown."""
        with self.lock:
            return self.health_cache.get(
                key,
                RuntimeCheckResult(
                    state=HEALTH_UNKNOWN,
                    ready=False,
                    fail_count=0,
                    success_count=0,
                    changed_at=0.0,
                    updated_at=0.0,
                    message=None,
                ),
            )


def update_desired_snapshot(state: RuntimeState, snapshot: dict[str, Any], carry_live_state: bool = False) -> int:
    """Store desired snapshot and set mtime-based generation.

    Inputs:
    - state: shared runtime state to update.
    - snapshot: newly loaded desired snapshot.
    - carry_live_state: when True, keep previous live_state.

    Output:
    - New desired generation value (from config mtime, integer seconds).
    """
    with state.lock:
        _apply_unresolved_backend_fallback(state, state.desired_snapshot, snapshot)
        _track_backend_ip_changes(state, state.desired_snapshot, snapshot)
        if carry_live_state:
            live_state = state.desired_snapshot.get("live_state")
            if live_state is not None:
                snapshot["live_state"] = live_state
        state.config_version_mtime = float(snapshot.get("config_version_mtime", 0.0))
        state.desired_generation = int(state.config_version_mtime)
        snapshot["desired_generation"] = state.desired_generation
        state.loaded_files_count = int(snapshot.get("loaded_files_count", 0))
        state.desired_snapshot = snapshot
        return state.desired_generation


def _frontend_backend_ip_index(snapshot: dict[str, Any]) -> dict[str, set[str]]:
    """Build frontend key -> backend IP set index from snapshot."""
    out: dict[str, set[str]] = {}
    for grp in snapshot.get("groups", []):
        for svc in grp.get("services", []):
            key = "|".join(
                [
                    str(svc.get("group", "")),
                    str(svc.get("frontend_name", "")),
                    str(svc.get("proto", "")),
                    str(svc.get("vip", "")),
                    str(svc.get("port", "")),
                ]
            )
            ips = {str(rs.get("ip")) for rs in svc.get("reals", []) if rs.get("ip") is not None}
            out[key] = ips
    return out


def _service_key(service: dict[str, Any]) -> str:
    """Build stable key for one service row."""
    return "|".join(
        [
            str(service.get("group", "")),
            str(service.get("frontend_name", "")),
            str(service.get("proto", "")),
            str(service.get("vip", "")),
            str(service.get("port", "")),
        ]
    )


def _is_ip_literal(value: str) -> bool:
    """Return True if value is a valid IP literal."""
    try:
        ipaddress.ip_address(value)
        return True
    except Exception:
        return False


def _service_index(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Build service key -> service row index."""
    out: dict[str, dict[str, Any]] = {}
    for grp in snapshot.get("groups", []):
        for svc in grp.get("services", []):
            out[_service_key(svc)] = svc
    return out


def _track_backend_resolve_error(state: RuntimeState, frontend_key: str) -> None:
    """Increment backend resolve error metrics."""
    now = time.time()
    state.backend_resolve_error_total += 1
    state.backend_resolve_error_per_frontend_total[frontend_key] = state.backend_resolve_error_per_frontend_total.get(frontend_key, 0) + 1
    state.backend_resolve_error_per_frontend_last_ts[frontend_key] = now


def _apply_unresolved_backend_fallback(state: RuntimeState, old_snapshot: dict[str, Any], new_snapshot: dict[str, Any]) -> None:
    """Keep old backend rules for services with unresolved hostnames.

    If any backend ip in a new service row is not an IP literal, treat it as unresolved
    hostname at load time and keep old backend reals (if available).
    """
    old_idx = _service_index(old_snapshot or {})
    for grp in new_snapshot.get("groups", []):
        for svc in grp.get("services", []):
            reals = svc.get("reals", [])
            unresolved = any(not _is_ip_literal(str(rs.get("ip", ""))) for rs in reals)
            if not unresolved:
                continue
            key = _service_key(svc)
            _track_backend_resolve_error(state, key)
            old_svc = old_idx.get(key)
            if old_svc and old_svc.get("reals"):
                svc["reals"] = copy.deepcopy(old_svc["reals"])


def _track_backend_ip_changes(state: RuntimeState, old_snapshot: dict[str, Any], new_snapshot: dict[str, Any]) -> None:
    """Track backend IP set changes per frontend and globally."""
    old_idx = _frontend_backend_ip_index(old_snapshot or {})
    new_idx = _frontend_backend_ip_index(new_snapshot or {})
    now = time.time()
    for key, new_ips in new_idx.items():
        old_ips = old_idx.get(key)
        if old_ips is None or old_ips == new_ips:
            continue
        state.backend_ip_change_total += 1
        state.backend_ip_change_per_frontend_total[key] = state.backend_ip_change_per_frontend_total.get(key, 0) + 1
        state.backend_ip_change_per_frontend_last_ts[key] = now


def read_desired_snapshot(state: RuntimeState, *, deep_copy: bool = False) -> dict[str, Any]:
    """Read current desired snapshot atomically.

    Input:
    - state: shared runtime state.
    - deep_copy: when True, return deep copy (safe for API handlers).

    Output:
    - Current desired snapshot (or a deep copy).
    """
    with state.lock:
        snap = state.desired_snapshot
        if not deep_copy:
            return snap
        return copy.deepcopy(snap)


def read_runtime_counters(state: RuntimeState) -> tuple[float, int, int, int, int, int, int]:
    """Read metrics counters atomically.

    Input:
    - state: shared runtime state.

    Output:
    - Tuple of (config_mtime, loaded_files, generation, queue_depth, drops, backend_ip_change_total, backend_resolve_error_total).
    """
    with state.lock:
        return (
            state.config_version_mtime,
            state.loaded_files_count,
            state.desired_generation,
            state.apply_queue_depth,
            state.coalesced_drops,
            state.backend_ip_change_total,
            state.backend_resolve_error_total,
        )


def read_backend_ip_change_metrics(state: RuntimeState) -> tuple[dict[str, int], dict[str, float]]:
    """Read per-frontend backend IP change counters and last timestamps."""
    with state.lock:
        return (
            dict(state.backend_ip_change_per_frontend_total),
            dict(state.backend_ip_change_per_frontend_last_ts),
        )


def read_backend_resolve_error_metrics(state: RuntimeState) -> tuple[dict[str, int], dict[str, float]]:
    """Read per-frontend backend resolve error counters and last timestamps."""
    with state.lock:
        return (
            dict(state.backend_resolve_error_per_frontend_total),
            dict(state.backend_resolve_error_per_frontend_last_ts),
        )
