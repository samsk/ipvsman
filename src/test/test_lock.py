"""Tests for lock helper."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.lock import ProcessLock


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


if __name__ == "__main__":
    unittest.main()
