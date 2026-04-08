"""Apply queue worker."""

from __future__ import annotations

import queue
import threading
import logging
from typing import Any

from src.ipvs_exec import LiveIpvsState, apply_plan
from src.reconcile import build_apply_plan, desired_services
from src.state import RuntimeState


class ApplyRuntime:
    """Single-thread apply runtime with coalescing."""

    def __init__(self, state: RuntimeState, log: logging.Logger) -> None:
        self._state = state
        self._log = log
        self._queue: "queue.Queue[tuple[int, dict[str, Any]]]" = queue.Queue(maxsize=1)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._stop_item: tuple[int, dict[str, Any]] = (-1, {})

    def start(self) -> None:
        """Start apply worker."""
        self._thread.start()

    def submit(self, generation: int, snapshot: dict[str, Any]) -> None:
        """Submit desired snapshot with coalescing."""
        try:
            self._queue.put_nowait((generation, snapshot))
        except queue.Full:
            try:
                self._queue.get_nowait()
                with self._state.lock:
                    self._state.coalesced_drops += 1
            except queue.Empty:
                pass
            self._queue.put_nowait((generation, snapshot))
        with self._state.lock:
            self._state.apply_queue_depth = self._queue.qsize()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                generation, snapshot = self._queue.get(timeout=0.5)
                with self._state.lock:
                    self._state.apply_queue_depth = self._queue.qsize()
            except queue.Empty:
                continue
            if generation < 0:
                continue
            with self._state.lock:
                desired_generation = self._state.desired_generation
            if generation < desired_generation:
                continue
            live = snapshot.get("live_state")
            if live is None:
                live = LiveIpvsState()
            plan = build_apply_plan(desired_services(snapshot), live)
            result = apply_plan(plan)
            if result.ok:
                with self._state.lock:
                    self._state.last_applied_snapshot = snapshot
            else:
                self._log.error("apply failed: %s", result.message)

    def stop(self, shutdown_timeout: float = 2.0) -> None:
        """Stop apply worker."""
        self._stop.set()
        try:
            self._queue.put_nowait(self._stop_item)
        except queue.Full:
            pass
        self._thread.join(timeout=max(0.1, shutdown_timeout))
