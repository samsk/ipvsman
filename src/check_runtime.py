"""Async healthcheck runtime."""

from __future__ import annotations

import concurrent.futures
import logging
import threading
import time
from typing import Any

from src import checks
from src.constants import HEALTH_HEALTHY, HEALTH_UNHEALTHY, HEALTH_UNKNOWN
from src.models import CheckTarget, HealthCheck, RuntimeCheckResult
from src.state import RuntimeState


def make_backend_health_key(group: str, frontend_name: str, backend_ip: str, backend_port: int) -> str:
    """Build unique health cache key.

    Inputs:
    - group: group name.
    - frontend_name: frontend identifier.
    - backend_ip: backend IP address.
    - backend_port: backend port.

    Output:
    - Stable cache key string.
    """
    return f"{group}|{frontend_name}|{backend_ip}|{int(backend_port)}"


def run_one_check(target: CheckTarget, check: HealthCheck) -> tuple[bool, str]:
    """Execute one check by type."""
    if check.type == "tcp":
        return checks.tcp_check(target, timeout=target.timeout or check.timeout)
    if check.type == "http":
        return checks.http_check(target, check, use_tls=False)
    if check.type == "https":
        return checks.http_check(target, check, use_tls=True)
    return checks.dns_check(target, check)


def is_check_due(next_due: dict[str, float], key: str, now: float, interval: float) -> bool:
    """Return True when interval elapsed since last scheduled check."""
    due = next_due.get(key, 0.0)
    return now >= due


def commit_next_check(next_due: dict[str, float], key: str, now: float, interval: float) -> None:
    """Record next due time after scheduling a check."""
    next_due[key] = now + max(0.1, interval)


def should_schedule_check(next_due: dict[str, float], key: str, now: float, interval: float) -> bool:
    """Decide if a backend check should run now (updates next_due when True)."""
    if not is_check_due(next_due, key, now, interval):
        return False
    commit_next_check(next_due, key, now, interval)
    return True


def update_health_state(
    previous: RuntimeCheckResult,
    ok: bool,
    now: float,
    rise: int,
    fall: int,
    message: str,
) -> RuntimeCheckResult:
    """Apply rise/fall transition logic."""
    success = previous.success_count + 1 if ok else 0
    fail = previous.fail_count + 1 if not ok else 0
    state = previous.state
    changed_at = previous.changed_at
    ready = True
    if ok and success >= rise and state != HEALTH_HEALTHY:
        state = HEALTH_HEALTHY
        changed_at = now
    elif (not ok) and fail >= fall and state != HEALTH_UNHEALTHY:
        state = HEALTH_UNHEALTHY
        changed_at = now
    if state == HEALTH_UNKNOWN:
        state = HEALTH_HEALTHY if ok else HEALTH_UNHEALTHY
        changed_at = now
    return RuntimeCheckResult(
        state=state,
        ready=ready,
        fail_count=fail,
        success_count=success,
        changed_at=changed_at,
        updated_at=now,
        message=message,
    )


class CheckRuntime:
    """Thread pool health-check scheduler."""

    def __init__(self, workers: int, state: RuntimeState, log: logging.Logger | None = None) -> None:
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers))
        self._state = state
        self._log = log
        self._next_due: dict[str, float] = {}
        self._schedule_lock = threading.Lock()
        self._backend_locks: dict[str, threading.Lock] = {}
        self._backend_locks_mutex = threading.Lock()
        self._pending = 0
        self._pending_lock = threading.Lock()
        w = max(1, workers)
        self._max_pending = max(64, w * 16)

    @staticmethod
    def _parse_key(key: str) -> tuple[str, str, str, int]:
        """Parse backend health key into parts."""
        parts = key.split("|", 3)
        if len(parts) != 4:
            return "unknown", "unknown", key, 0
        group, frontend, backend_ip, backend_port_txt = parts
        try:
            backend_port = int(backend_port_txt)
        except ValueError:
            backend_port = 0
        return group, frontend, backend_ip, backend_port

    def _lock_for_backend(self, key: str) -> threading.Lock:
        """Serialize probe + health RMW per backend key."""
        with self._backend_locks_mutex:
            lock = self._backend_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._backend_locks[key] = lock
            return lock

    def check_service(self, service: dict[str, Any]) -> None:
        """Run checks for all backends in one service."""
        hc = HealthCheck.model_validate(service["healthcheck"])
        if hc.disable:
            return
        if service.get("disabled"):
            return
        now = time.time()
        for backend in service.get("reals", []):
            key = make_backend_health_key(
                str(service["group"]),
                str(service["frontend_name"]),
                str(backend["ip"]),
                int(backend["port"]),
            )
            with self._schedule_lock:
                if not should_schedule_check(self._next_due, key, now, hc.interval):
                    continue
            with self._pending_lock:
                if self._pending >= self._max_pending:
                    continue
                self._pending += 1
            target = CheckTarget.model_validate(backend["check_target"])

            def _wrap() -> None:
                try:
                    self._run_backend(key, target, hc)
                finally:
                    with self._pending_lock:
                        self._pending -= 1

            self._pool.submit(_wrap)

    def _run_backend(self, key: str, target: CheckTarget, hc: HealthCheck) -> None:
        with self._lock_for_backend(key):
            now = time.time()
            prev = self._state.get_health(key)
            ok, message = run_one_check(target, hc)
            next_state = update_health_state(prev, ok, now, hc.rise, hc.fall, message)
            self._state.set_health(key, next_state)
            if self._log is None:
                return
            group, frontend, backend_ip, backend_port = self._parse_key(key)
            if (not ok) and prev.fail_count == 0:
                self._log.info(
                    "NOTICE healthcheck start failing group=%s frontend=%s backend=%s:%s msg=%s",
                    group,
                    frontend,
                    backend_ip,
                    backend_port,
                    message,
                )
            if next_state.state == HEALTH_UNHEALTHY and prev.state != HEALTH_UNHEALTHY:
                self._log.critical(
                    "ALERT backend disabled by healthcheck group=%s frontend=%s backend=%s:%s",
                    group,
                    frontend,
                    backend_ip,
                    backend_port,
                )

    def stop(self) -> None:
        """Stop worker pool."""
        self._pool.shutdown(wait=True)
