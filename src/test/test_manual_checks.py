"""Tests for manual check trigger."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from src.manual_checks import run_manual_checks


class ManualChecksTest(unittest.TestCase):
    def test_manual_checks_filtering(self) -> None:
        snapshot = {
            "groups": [
                {
                    "group": "g1",
                    "services": [
                        {
                            "group": "g1",
                            "frontend_name": "f1",
                            "healthcheck": {"type": "tcp", "interval": 1, "timeout": 1, "rise": 1, "fall": 1},
                            "reals": [
                                {"ip": "10.0.0.1", "check_target": {"ip": "10.0.0.1", "port": 80, "type": "tcp"}},
                                {"ip": "10.0.0.2", "check_target": {"ip": "10.0.0.2", "port": 80, "type": "tcp"}},
                            ],
                        }
                    ],
                }
            ]
        }
        with patch("src.manual_checks.run_one_check", return_value=(True, "ok")):
            out = run_manual_checks(snapshot, group="g1", backend_ip="10.0.0.2")
        self.assertEqual(out["total"], 1)
        self.assertEqual(out["ok"], 1)
        self.assertEqual(out["results"][0]["backend_ip"], "10.0.0.2")

    def test_manual_checks_skip_disabled_service(self) -> None:
        snapshot = {
            "groups": [
                {
                    "group": "g1",
                    "services": [
                        {
                            "group": "g1",
                            "frontend_name": "f1",
                            "disabled": True,
                            "healthcheck": {"type": "tcp", "interval": 1, "timeout": 1, "rise": 1, "fall": 1},
                            "reals": [{"ip": "10.0.0.1", "check_target": {"ip": "10.0.0.1", "port": 80, "type": "tcp"}}],
                        }
                    ],
                }
            ]
        }
        with patch("src.manual_checks.run_one_check", return_value=(True, "ok")) as mocked:
            out = run_manual_checks(snapshot, group=None, backend_ip=None)
        self.assertEqual(out["total"], 0)
        self.assertEqual(mocked.call_count, 0)

    def test_manual_checks_failure_counts(self) -> None:
        snapshot = {
            "groups": [
                {
                    "group": "g1",
                    "services": [
                        {
                            "group": "g1",
                            "frontend_name": "f1",
                            "healthcheck": {"type": "tcp", "interval": 1, "timeout": 1, "rise": 1, "fall": 1},
                            "reals": [{"ip": "10.0.0.1", "check_target": {"ip": "10.0.0.1", "port": 80, "type": "tcp"}}],
                        }
                    ],
                }
            ]
        }
        with patch("src.manual_checks.run_one_check", return_value=(False, "fail")):
            out = run_manual_checks(snapshot, group=None, backend_ip=None)
        self.assertEqual(out["failed"], 1)

    def test_manual_checks_skip_healthcheck_disabled_without_explicit_target(self) -> None:
        snapshot = {
            "groups": [
                {
                    "group": "g1",
                    "services": [
                        {
                            "group": "g1",
                            "frontend_name": "f1",
                            "healthcheck": {"type": "tcp", "disable": True, "interval": 1, "timeout": 1, "rise": 1, "fall": 1},
                            "reals": [{"ip": "10.0.0.1", "check_target": {"ip": "10.0.0.1", "port": 80, "type": "tcp"}}],
                        }
                    ],
                }
            ]
        }
        with patch("src.manual_checks.run_one_check", return_value=(True, "ok")) as mocked:
            out = run_manual_checks(snapshot, group=None, backend_ip=None)
        self.assertEqual(out["total"], 0)
        self.assertEqual(mocked.call_count, 0)

    def test_manual_checks_allow_healthcheck_disabled_when_explicitly_targeted(self) -> None:
        snapshot = {
            "groups": [
                {
                    "group": "g1",
                    "services": [
                        {
                            "group": "g1",
                            "frontend_name": "f1",
                            "healthcheck": {"type": "tcp", "disable": True, "interval": 1, "timeout": 1, "rise": 1, "fall": 1},
                            "reals": [{"ip": "10.0.0.1", "check_target": {"ip": "10.0.0.1", "port": 80, "type": "tcp"}}],
                        }
                    ],
                }
            ]
        }
        with patch("src.manual_checks.run_one_check", return_value=(True, "ok")) as mocked:
            out = run_manual_checks(snapshot, group="g1", backend_ip=None)
        self.assertEqual(out["total"], 1)
        self.assertEqual(mocked.call_count, 1)


if __name__ == "__main__":
    unittest.main()
