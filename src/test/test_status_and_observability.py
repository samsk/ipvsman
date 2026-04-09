"""Tests for status and detailed observability outputs."""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout

from src.cli_observability import print_detailed
from src.ipvs_exec import LiveIpvsState, RealServer, VirtualService
from src.status_cmd import print_status


class StatusObservabilityTest(unittest.TestCase):
    def _snapshot(self) -> dict:
        return {
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
                            "scheduler": "wrr",
                            "reals": [
                                {"ip": "10.0.0.1", "port": 80, "weight": 1},
                                {"ip": "10.0.0.2", "port": 80, "weight": 0},
                            ],
                        }
                    ],
                }
            ],
            "desired_generation": 7,
            "config_version_mtime": 123.0,
        }

    def test_status_exit_code_degraded(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = print_status(self._snapshot(), LiveIpvsState(), output="table")
        self.assertEqual(rc, 1)
        out = buf.getvalue()
        self.assertIn("status=DEGRADED", out)
        self.assertIn("[DEGRADED] tcp 127.0.0.1:80", out)
        self.assertIn("backends_up=1/2", out)

    def test_detailed_json_filter_backend(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            print_detailed(
                self._snapshot(),
                LiveIpvsState(),
                output="json",
                show_counters=False,
                only_active=False,
                filter_backend="10.0.0.1",
            )
        payload = json.loads(buf.getvalue())
        self.assertEqual(len(payload["services"]), 1)
        self.assertEqual(len(payload["services"][0]["backends"]), 1)
        self.assertEqual(payload["services"][0]["backends"][0]["ip"], "10.0.0.1")

    def test_detailed_table_output_improved_format(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            print_detailed(
                self._snapshot(),
                LiveIpvsState(),
                output="table",
                show_counters=True,
                only_active=False,
            )
        out = buf.getvalue()
        self.assertIn("status-detailed services=1", out)
        self.assertIn("[DEGRADED] g1/f1 tcp 127.0.0.1:80", out)
        self.assertIn("backends_up=1/2", out)
        self.assertIn("  - [UP] 10.0.0.1:80", out)
        self.assertIn("pkts_in=0 pkts_out=0", out)

    def test_status_shows_actual_desired_when_different(self) -> None:
        live = LiveIpvsState(
            services=[
                VirtualService(
                    proto="tcp",
                    vip="127.0.0.1",
                    port=80,
                    scheduler="wrr",
                    reals=[RealServer(ip="10.0.0.1", port=80, weight=0)],
                )
            ]
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = print_status(self._snapshot(), live, output="table")
        self.assertEqual(rc, 1)
        out = buf.getvalue()
        self.assertIn("weight=0/1", out)

    def test_detailed_shows_actual_desired_when_different(self) -> None:
        live = LiveIpvsState(
            services=[
                VirtualService(
                    proto="tcp",
                    vip="127.0.0.1",
                    port=80,
                    scheduler="wrr",
                    reals=[RealServer(ip="10.0.0.1", port=80, weight=0)],
                )
            ]
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            print_detailed(self._snapshot(), live, output="table", show_counters=False, only_active=False)
        out = buf.getvalue()
        self.assertIn("weight=0/1 (actual/desired)", out)


if __name__ == "__main__":
    unittest.main()
