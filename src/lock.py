"""File lock helpers."""

from __future__ import annotations

import fcntl
import os
from pathlib import Path


class ProcessLock:
    """Non-blocking process lock."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fh = None

    def acquire(self) -> bool:
        """Acquire lock, return True on success."""
        if self._fh is not None:
            try:
                self._fh.close()
            except OSError:
                pass
            self._fh = None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Save owner pid for external helpers (reload action/systemd).
            self._fh.seek(0)
            self._fh.truncate(0)
            self._fh.write(f"{os.getpid()}\n")
            self._fh.flush()
            return True
        except OSError:
            self._fh.close()
            self._fh = None
            return False

    def release(self) -> None:
        """Release lock."""
        if self._fh is None:
            return
        try:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        self._fh.close()
        self._fh = None


def read_lock_pid(path: Path) -> int | None:
    """Read daemon PID from lock file.

    Input:
    - path: Lock file path.

    Output:
    - PID from file, or None when missing/invalid.
    """
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text:
        return None
    try:
        pid = int(text)
    except ValueError:
        return None
    if pid <= 0:
        return None
    return pid
