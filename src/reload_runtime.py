"""Reload thread runtime."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

from src.loader import load_snapshot
from src.state import RuntimeState, update_desired_snapshot


class ReloadRuntime:
    """Background config reload worker."""

    def __init__(self, config_dir: Path, state: RuntimeState, log: logging.Logger) -> None:
        self._config_dir = config_dir
        self._state = state
        self._log = log
        self._reload_event = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        """Start reload thread."""
        self._thread.start()

    def trigger(self) -> None:
        """Trigger reload."""
        self._reload_event.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            self._reload_event.wait(timeout=0.5)
            if not self._reload_event.is_set():
                continue
            self._reload_event.clear()
            try:
                snap = load_snapshot(self._config_dir)
                generation = update_desired_snapshot(self._state, snap, carry_live_state=True)
                with self._state.lock:
                    self._state.last_reload_at = time.time()
                    self._state.last_reload_error = None
                for w in snap.get("warnings", []):
                    self._log.warning("reload warning: %s", w)
                self._log.info("reload ok generation=%s", generation)
            except Exception as exc:
                with self._state.lock:
                    self._state.last_reload_error = str(exc)
                self._log.error("reload failed: %s", exc)

    def stop(self, shutdown_timeout: float = 2.0) -> None:
        """Stop reload thread."""
        self._stop.set()
        self._reload_event.set()
        self._thread.join(timeout=max(0.1, shutdown_timeout))
