"""Tests for runtime snapshot helper."""

from __future__ import annotations

import unittest

from src.ipvs_exec import LiveIpvsState
from src.main import _runtime_snapshot_with_live


class MainSnapshotTest(unittest.TestCase):
    def test_runtime_snapshot_is_deep_copy(self) -> None:
        base = {
            "groups": [
                {
                    "services": [
                        {
                            "group": "g1",
                            "frontend_name": "f1",
                            "reals": [{"ip": "10.0.0.1", "weight": 1, "configured_weight": 1}],
                        }
                    ]
                }
            ]
        }
        snap = _runtime_snapshot_with_live(base, LiveIpvsState())
        snap["groups"][0]["services"][0]["reals"][0]["weight"] = 0
        self.assertEqual(base["groups"][0]["services"][0]["reals"][0]["weight"], 1)


if __name__ == "__main__":
    unittest.main()
