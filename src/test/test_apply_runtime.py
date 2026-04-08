"""Tests for apply runtime worker."""

from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from src.apply_runtime import ApplyRuntime
from src.ipvs_exec import IpvsApplyResult, LiveIpvsState
from src.state import RuntimeState


class ApplyRuntimeTest(unittest.TestCase):
    def test_submit_coalesces_queue(self) -> None:
        state = RuntimeState()
        log = Mock()
        runtime = ApplyRuntime(state, log)
        runtime.submit(1, {"groups": []})
        runtime.submit(2, {"groups": []})
        self.assertGreaterEqual(state.coalesced_drops, 1)
        self.assertEqual(state.apply_queue_depth, 1)

    def test_apply_failure_logs_error(self) -> None:
        state = RuntimeState()
        state.desired_generation = 1
        log = Mock()
        runtime = ApplyRuntime(state, log)
        with patch("src.apply_runtime.apply_plan", return_value=IpvsApplyResult(ok=False, message="boom")):
            runtime.start()
            try:
                runtime.submit(1, {"groups": [], "live_state": LiveIpvsState()})
            finally:
                runtime.stop()
        log.error.assert_called()

    def test_apply_missing_live_state_uses_empty(self) -> None:
        state = RuntimeState()
        state.desired_generation = 1
        log = Mock()
        runtime = ApplyRuntime(state, log)
        with patch("src.apply_runtime.apply_plan", return_value=IpvsApplyResult(ok=True, message="ok")) as mock_apply:
            runtime.start()
            try:
                runtime.submit(1, {"groups": []})
            finally:
                runtime.stop()
        mock_apply.assert_called_once()

    def test_stale_generation_is_skipped(self) -> None:
        state = RuntimeState()
        state.desired_generation = 2
        log = Mock()
        runtime = ApplyRuntime(state, log)
        with patch("src.apply_runtime.apply_plan") as mock_apply:
            runtime.start()
            try:
                runtime.submit(1, {"groups": [], "live_state": LiveIpvsState()})
            finally:
                runtime.stop()
        mock_apply.assert_not_called()


if __name__ == "__main__":
    unittest.main()
