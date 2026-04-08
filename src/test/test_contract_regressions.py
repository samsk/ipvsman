"""Regression tests for previously fixed behaviour."""

from __future__ import annotations

import importlib.util
import time
import unittest

from src.constants import HEALTH_UNHEALTHY
from src.checks import _NoRedirect
from src.main import _apply_health_weights
from src.models import ApiConfigPut, Group, RuntimeCheckResult
from src.state import RuntimeState


class ContractRegressionsTest(unittest.TestCase):
    def test_http_probe_redirects_disabled(self) -> None:
        """Regression: urllib must not follow redirects for config-driven URLs."""
        self.assertIsNone(_NoRedirect().redirect_request())

    def test_stale_health_uses_configured_weight(self) -> None:
        """Regression: stale_grace_sec ignores old UNHEALTHY when probe data is stale."""
        state = RuntimeState()
        state.started_at = 0.0
        key = "g|f|10.0.0.1|80"
        old = time.time() - 500.0
        state.set_health(
            key,
            RuntimeCheckResult(
                state=HEALTH_UNHEALTHY,
                ready=True,
                fail_count=1,
                success_count=0,
                changed_at=old,
                updated_at=old,
                message="down",
            ),
        )
        snapshot = {
            "groups": [
                {
                    "group": "g",
                    "services": [
                        {
                            "group": "g",
                            "frontend_name": "f",
                            "reals": [{"ip": "10.0.0.1", "port": 80, "weight": 0, "configured_weight": 7}],
                        }
                    ],
                }
            ],
        }
        _apply_health_weights(snapshot, state, cold_start_sec=0.0, stale_grace_sec=60.0)
        self.assertEqual(
            snapshot["groups"][0]["services"][0]["reals"][0]["weight"],
            7,
        )

    def test_fresh_unhealthy_still_zero_weight(self) -> None:
        state = RuntimeState()
        state.started_at = 0.0
        key = "g|f|10.0.0.1|80"
        now = time.time()
        state.set_health(
            key,
            RuntimeCheckResult(
                state=HEALTH_UNHEALTHY,
                ready=True,
                fail_count=3,
                success_count=0,
                changed_at=now,
                updated_at=now,
                message="down",
            ),
        )
        snapshot = {
            "groups": [
                {
                    "group": "g",
                    "services": [
                        {
                            "group": "g",
                            "frontend_name": "f",
                            "reals": [{"ip": "10.0.0.1", "port": 80, "weight": 5, "configured_weight": 7}],
                        }
                    ],
                }
            ],
        }
        _apply_health_weights(snapshot, state, cold_start_sec=0.0, stale_grace_sec=3600.0)
        self.assertEqual(snapshot["groups"][0]["services"][0]["reals"][0]["weight"], 0)

    def test_api_config_put_rejects_invalid_healthcheck_type(self) -> None:
        """Regression: healthcheck.type must match schema (not loose dicts)."""
        if importlib.util.find_spec("pydantic") is None:
            self.skipTest("pydantic required for strict API validation")
        with self.assertRaises(Exception):
            ApiConfigPut.model_validate(
                {
                    "groups": [
                        {
                            "group": "x",
                            "vip": "127.0.0.1",
                            "frontends": [{"name": "a", "proto": "tcp", "port": 80}],
                            "backends": [{"ip": "10.0.0.1", "weight": 1}],
                            "healthcheck": {"type": "not-a-valid-probe-type"},
                        }
                    ]
                }
            )

    def test_api_config_put_groups_roundtrip_through_group_model(self) -> None:
        """Regression: each group must satisfy Group schema (same as YAML loader)."""
        if importlib.util.find_spec("pydantic") is None:
            self.skipTest("pydantic required for Group model_validate")
        payload = ApiConfigPut.model_validate(
            {
                "groups": [
                    {
                        "group": "x",
                        "vip": "127.0.0.1",
                        "frontends": [{"name": "a", "proto": "tcp", "port": 80}],
                        "backends": [{"ip": "10.0.0.1", "weight": 1}],
                        "healthcheck": {"type": "tcp"},
                    }
                ]
            }
        )
        raw = payload.groups[0]
        g = Group.model_validate(raw)
        self.assertEqual(g.group, "x")


if __name__ == "__main__":
    unittest.main()
