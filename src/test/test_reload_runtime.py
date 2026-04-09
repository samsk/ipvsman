"""Tests for config reload thread."""

from __future__ import annotations

import tempfile
import time
import unittest
import os
from pathlib import Path
from unittest.mock import Mock, patch

from src.ipvs_exec import LiveIpvsState
from src.reload_runtime import ReloadRuntime
from src.state import RuntimeState


class ReloadRuntimeTest(unittest.TestCase):
    def test_reload_sets_generation_from_mtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for d in ("groups", "backends", "backend-maps", "check-refs"):
                (root / d).mkdir()
            (root / "groups" / "g.yaml").write_text(
                """
- group: g1
  vip: 127.0.0.1
  frontends:
    - name: f1
      proto: tcp
      port: 80
  backends:
    - ip: 10.0.0.1
      weight: 1
  healthcheck:
    type: tcp
""",
                encoding="utf-8",
            )
            state = RuntimeState()
            log = Mock()
            rt = ReloadRuntime(root, state, log)
            rt.start()
            try:
                rt.trigger()
                expected_generation = int((root / "groups" / "g.yaml").stat().st_mtime)
                for _ in range(100):
                    if state.desired_generation == expected_generation and state.last_reload_error is None:
                        break
                    time.sleep(0.02)
                self.assertEqual(state.desired_generation, expected_generation)
                self.assertIsNone(state.last_reload_error)
            finally:
                rt.stop(2.0)

    def test_reload_preserves_live_state(self) -> None:
        """carry_live_state keeps prior live snapshot for API/apply."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for d in ("groups", "backends", "backend-maps", "check-refs"):
                (root / d).mkdir()
            (root / "groups" / "g.yaml").write_text(
                """
- group: g1
  vip: 127.0.0.1
  frontends:
    - name: f1
      proto: tcp
      port: 80
  backends:
    - ip: 10.0.0.1
      weight: 1
  healthcheck:
    type: tcp
""",
                encoding="utf-8",
            )
            state = RuntimeState()
            marker = LiveIpvsState()
            state.desired_snapshot = {"groups": [], "live_state": marker}
            log = Mock()
            rt = ReloadRuntime(root, state, log)
            rt.start()
            try:
                rt.trigger()
                expected_generation = int((root / "groups" / "g.yaml").stat().st_mtime)
                for _ in range(100):
                    if state.desired_generation == expected_generation and state.last_reload_error is None:
                        break
                    time.sleep(0.02)
                self.assertEqual(state.desired_generation, expected_generation)
                self.assertIs(state.desired_snapshot.get("live_state"), marker)
            finally:
                rt.stop(2.0)

    def test_reload_logs_when_generation_goes_backwards(self) -> None:
        """Log warning when mtime-based generation decreases."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for d in ("groups", "backends", "backend-maps", "check-refs"):
                (root / d).mkdir()
            gfile = root / "groups" / "g.yaml"
            gfile.write_text(
                """
- group: g1
  vip: 127.0.0.1
  frontends:
    - name: f1
      proto: tcp
      port: 80
  backends:
    - ip: 10.0.0.1
      weight: 1
  healthcheck:
    type: tcp
""",
                encoding="utf-8",
            )
            now = time.time()
            first_mtime = int(now)
            second_mtime = int(now) - 10
            gfile.touch()
            os.utime(gfile, (first_mtime, first_mtime))
            state = RuntimeState()
            log = Mock()
            rt = ReloadRuntime(root, state, log)
            rt.start()
            try:
                rt.trigger()
                for _ in range(100):
                    if state.desired_generation == first_mtime and state.last_reload_error is None:
                        break
                    time.sleep(0.02)
                self.assertEqual(state.desired_generation, first_mtime)
                os.utime(gfile, (second_mtime, second_mtime))
                rt.trigger()
                for _ in range(100):
                    if state.desired_generation == second_mtime and state.last_reload_error is None:
                        break
                    time.sleep(0.02)
                self.assertEqual(state.desired_generation, second_mtime)
                self.assertTrue(
                    any(
                        call.args
                        and call.args[0] == "reload generation moved backwards prev=%s new=%s"
                        and call.args[1:] == (first_mtime, second_mtime)
                        for call in log.warning.call_args_list
                    )
                )
            finally:
                rt.stop(2.0)

    def test_reload_logs_ok_only_when_generation_changes(self) -> None:
        """Log reload success only when generation changes."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for d in ("groups", "backends", "backend-maps", "check-refs"):
                (root / d).mkdir()
            (root / "groups" / "g.yaml").write_text(
                """
- group: g1
  vip: 127.0.0.1
  frontends:
    - name: f1
      proto: tcp
      port: 80
  backends:
    - ip: 10.0.0.1
      weight: 1
  healthcheck:
    type: tcp
""",
                encoding="utf-8",
            )
            state = RuntimeState()
            log = Mock()
            rt = ReloadRuntime(root, state, log)
            rt.start()
            try:
                rt.trigger()
                first_generation = int((root / "groups" / "g.yaml").stat().st_mtime)
                for _ in range(100):
                    if state.desired_generation == first_generation and state.last_reload_error is None:
                        break
                    time.sleep(0.02)
                self.assertEqual(state.desired_generation, first_generation)
                with state.lock:
                    first_reload_at = float(state.last_reload_at)

                rt.trigger()
                for _ in range(100):
                    with state.lock:
                        second_reload_at = float(state.last_reload_at)
                    if second_reload_at > first_reload_at and state.last_reload_error is None:
                        break
                    time.sleep(0.02)

                reload_ok_calls = [
                    call
                    for call in log.info.call_args_list
                    if call.args and call.args[0] == "reload ok prev_generation=%s generation=%s"
                ]
                self.assertEqual(len(reload_ok_calls), 1)
            finally:
                rt.stop(2.0)

    def test_reload_applies_small_random_skew_before_load(self) -> None:
        """Sleep with jitter bounded to 0.5s."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for d in ("groups", "backends", "backend-maps", "check-refs"):
                (root / d).mkdir()
            (root / "groups" / "g.yaml").write_text(
                """
- group: g1
  vip: 127.0.0.1
  frontends:
    - name: f1
      proto: tcp
      port: 80
  backends:
    - ip: 10.0.0.1
      weight: 1
  healthcheck:
    type: tcp
""",
                encoding="utf-8",
            )
            state = RuntimeState()
            log = Mock()
            with patch("src.reload_runtime.random.uniform", return_value=0.25) as jitter_mock:
                rt = ReloadRuntime(root, state, log)
                rt.start()
                try:
                    rt.trigger()
                    for _ in range(100):
                        if jitter_mock.call_args_list:
                            break
                        time.sleep(0.02)
                    jitter_mock.assert_any_call(0.0, 0.5)
                finally:
                    rt.stop(2.0)


if __name__ == "__main__":
    unittest.main()
