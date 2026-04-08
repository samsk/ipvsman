"""Tests for shared runtime state."""

from __future__ import annotations

import unittest

from src.state import RuntimeState, read_desired_snapshot, update_desired_snapshot


class StateTest(unittest.TestCase):
    def test_read_desired_snapshot_deep_copy_is_isolated(self) -> None:
        state = RuntimeState()
        state.desired_snapshot = {"groups": [{"services": []}], "x": 1}
        a = read_desired_snapshot(state, deep_copy=True)
        b = read_desired_snapshot(state, deep_copy=True)
        a["x"] = 2
        self.assertEqual(b["x"], 1)
        self.assertEqual(state.desired_snapshot["x"], 1)

    def test_backend_ip_change_tracking_increments(self) -> None:
        state = RuntimeState()
        snap1 = {
            "groups": [
                {
                    "group": "g1",
                    "services": [
                        {
                            "group": "g1",
                            "frontend_name": "f1",
                            "proto": "tcp",
                            "vip": "127.0.0.1",
                            "port": 80,
                            "reals": [{"ip": "10.0.0.1"}],
                        }
                    ],
                }
            ]
        }
        snap2 = {
            "groups": [
                {
                    "group": "g1",
                    "services": [
                        {
                            "group": "g1",
                            "frontend_name": "f1",
                            "proto": "tcp",
                            "vip": "127.0.0.1",
                            "port": 80,
                            "reals": [{"ip": "10.0.0.2"}],
                        }
                    ],
                }
            ]
        }
        update_desired_snapshot(state, snap1)
        update_desired_snapshot(state, snap2)
        self.assertEqual(state.backend_ip_change_total, 1)

    def test_unresolved_backend_keeps_old_reals_and_counts_error(self) -> None:
        state = RuntimeState()
        snap1 = {
            "groups": [
                {
                    "group": "g1",
                    "services": [
                        {
                            "group": "g1",
                            "frontend_name": "f1",
                            "proto": "tcp",
                            "vip": "127.0.0.1",
                            "port": 80,
                            "reals": [{"ip": "10.0.0.1", "port": 80, "weight": 1}],
                        }
                    ],
                }
            ]
        }
        snap2 = {
            "groups": [
                {
                    "group": "g1",
                    "services": [
                        {
                            "group": "g1",
                            "frontend_name": "f1",
                            "proto": "tcp",
                            "vip": "127.0.0.1",
                            "port": 80,
                            "reals": [{"ip": "backend.example.local", "port": 80, "weight": 1}],
                        }
                    ],
                }
            ]
        }
        update_desired_snapshot(state, snap1)
        update_desired_snapshot(state, snap2)
        reals = state.desired_snapshot["groups"][0]["services"][0]["reals"]
        self.assertEqual(reals[0]["ip"], "10.0.0.1")
        self.assertEqual(state.backend_resolve_error_total, 1)


if __name__ == "__main__":
    unittest.main()
