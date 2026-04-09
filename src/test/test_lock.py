"""Tests for lock helper."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.lock import ProcessLock, read_lock_pid


class LockTest(unittest.TestCase):
    def test_nonblocking_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.lock"
            a = ProcessLock(path)
            b = ProcessLock(path)
            self.assertTrue(a.acquire())
            self.assertFalse(b.acquire())
            a.release()
            self.assertTrue(b.acquire())
            b.release()

    def test_acquire_writes_pid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.lock"
            lock = ProcessLock(path)
            with patch("src.lock.os.getpid", return_value=4321):
                self.assertTrue(lock.acquire())
            try:
                self.assertEqual(path.read_text(encoding="utf-8").strip(), "4321")
            finally:
                lock.release()

    def test_read_lock_pid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.lock"
            path.write_text("1234\n", encoding="utf-8")
            self.assertEqual(read_lock_pid(path), 1234)

    def test_read_lock_pid_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.lock"
            path.write_text("bad\n", encoding="utf-8")
            self.assertIsNone(read_lock_pid(path))


if __name__ == "__main__":
    unittest.main()
