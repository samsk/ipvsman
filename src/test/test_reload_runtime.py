"""Tests for config reload thread."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock

from src.ipvs_exec import LiveIpvsState
from src.reload_runtime import ReloadRuntime
from src.state import RuntimeState


class ReloadRuntimeTest(unittest.TestCase):
    def test_reload_bumps_generation(self) -> None:
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
                for _ in range(100):
                    if state.desired_generation > 0 and state.last_reload_error is None:
                        break
                    time.sleep(0.02)
                self.assertGreater(state.desired_generation, 0)
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
                for _ in range(100):
                    if state.desired_generation > 0 and state.last_reload_error is None:
                        break
                    time.sleep(0.02)
                self.assertGreater(state.desired_generation, 0)
                self.assertIs(state.desired_snapshot.get("live_state"), marker)
            finally:
                rt.stop(2.0)


if __name__ == "__main__":
    unittest.main()
