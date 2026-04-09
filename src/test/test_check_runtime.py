"""Tests for health check runtime."""

from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import Mock, patch

from src.check_runtime import CheckRuntime, update_health_state
from src.models import HealthCheck, RuntimeCheckResult
from src.constants import HEALTH_HEALTHY, HEALTH_UNHEALTHY, HEALTH_UNKNOWN
from src.models import CheckTarget
from src.state import RuntimeState


class CheckRuntimeTest(unittest.TestCase):
    def test_update_health_state_unknown_resolves_first_ok(self) -> None:
        prev = RuntimeCheckResult(
            state=HEALTH_UNKNOWN,
            ready=False,
            fail_count=0,
            success_count=0,
            changed_at=0.0,
            updated_at=0.0,
            message=None,
        )
        out = update_health_state(prev, True, now=100.0, rise=2, fall=3, message="ok")
        self.assertEqual(out.state, HEALTH_HEALTHY)
        self.assertEqual(out.changed_at, 100.0)

    def test_update_health_state_rise_after_unhealthy(self) -> None:
        prev = RuntimeCheckResult(
            state=HEALTH_UNHEALTHY,
            ready=True,
            fail_count=3,
            success_count=0,
            changed_at=1.0,
            updated_at=1.0,
            message="down",
        )
        one = update_health_state(prev, True, now=10.0, rise=2, fall=3, message="ok")
        self.assertEqual(one.state, HEALTH_UNHEALTHY)
        two = update_health_state(one, True, now=11.0, rise=2, fall=3, message="ok")
        self.assertEqual(two.state, HEALTH_HEALTHY)

    def test_same_backend_key_no_overlapping_probes(self) -> None:
        state = RuntimeState()
        runtime = CheckRuntime(4, state)
        target = CheckTarget(ip="127.0.0.1", port=1, type="tcp")
        hc = HealthCheck.model_validate(
            {"type": "tcp", "interval": 0.1, "timeout": 1.0, "rise": 1, "fall": 1}
        )
        key = "g|f|127.0.0.1|1"
        active = [0]
        max_active = [0]

        def slow_check(*_a: object, **_k: object) -> tuple[bool, str]:
            active[0] += 1
            max_active[0] = max(max_active[0], active[0])
            time.sleep(0.1)
            active[0] -= 1
            return True, "ok"

        with patch("src.check_runtime.run_one_check", side_effect=slow_check):
            t1 = threading.Thread(target=runtime._run_backend, args=(key, target, hc))
            t2 = threading.Thread(target=runtime._run_backend, args=(key, target, hc))
            t1.start()
            t2.start()
            t1.join()
            t2.join()

        self.assertEqual(max_active[0], 1, "probes for same key must not overlap")

    def test_check_service_skips_when_service_disabled(self) -> None:
        state = RuntimeState()
        runtime = CheckRuntime(2, state)
        service = {
            "group": "g",
            "frontend_name": "f",
            "disabled": True,
            "healthcheck": {"type": "tcp", "interval": 1, "timeout": 1, "rise": 1, "fall": 1},
            "reals": [
                {
                    "ip": "127.0.0.1",
                    "port": 80,
                    "check_target": {"ip": "127.0.0.1", "port": 80, "type": "tcp"},
                }
            ],
        }
        with patch("src.check_runtime.run_one_check") as mocked:
            runtime.check_service(service)
            self.assertEqual(mocked.call_count, 0)

    def test_check_service_skips_when_healthcheck_disabled(self) -> None:
        state = RuntimeState()
        runtime = CheckRuntime(2, state)
        service = {
            "group": "g",
            "frontend_name": "f",
            "healthcheck": {"type": "tcp", "disable": True},
            "reals": [
                {
                    "ip": "127.0.0.1",
                    "port": 80,
                    "check_target": {"ip": "127.0.0.1", "port": 80, "type": "tcp"},
                }
            ],
        }
        with patch("src.check_runtime.run_one_check") as mocked:
            runtime.check_service(service)
            self.assertEqual(mocked.call_count, 0)

    def test_logs_notice_and_alert_on_fail_to_unhealthy(self) -> None:
        state = RuntimeState()
        log = Mock()
        runtime = CheckRuntime(1, state, log=log)
        target = CheckTarget(ip="127.0.0.1", port=80, type="tcp")
        hc = HealthCheck.model_validate({"type": "tcp", "interval": 1, "timeout": 1, "rise": 1, "fall": 1})
        key = "g1|f1|10.0.0.1|80"
        with patch("src.check_runtime.run_one_check", return_value=(False, "timeout")):
            runtime._run_backend(key, target, hc)
        log.info.assert_called_once()
        log.critical.assert_called_once()


if __name__ == "__main__":
    unittest.main()
