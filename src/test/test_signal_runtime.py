"""Tests for signal handlers."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from src.signal_runtime import install_handlers


class SignalRuntimeTest(unittest.TestCase):
    def test_install_registers_three_signals(self) -> None:
        with patch("signal.signal") as sig:
            install_handlers(on_reload=lambda: None, on_stop=lambda: None)
            self.assertEqual(sig.call_count, 3)


if __name__ == "__main__":
    unittest.main()
