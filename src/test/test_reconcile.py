"""Tests for reconcile helpers."""

from __future__ import annotations

import unittest

from src.ipvs_exec import LiveIpvsState, RealServer, VirtualService
from src.reconcile import build_apply_plan, build_report, desired_services


class ReconcileTest(unittest.TestCase):
    def test_build_report_includes_group_and_frontend(self) -> None:
        snapshot = {
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
                            "reals": [{"ip": "10.0.0.1", "port": 80, "weight": 1}],
                        }
                    ],
                }
            ],
            "desired_generation": 2,
            "config_version_mtime": 5.0,
        }
        report = build_report(snapshot, LiveIpvsState())
        self.assertEqual(report["services"][0]["group"], "g1")

    def test_build_report_none_live_treats_as_empty(self) -> None:
        snapshot = {
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
                            "reals": [{"ip": "10.0.0.1", "port": 80, "weight": 1}],
                        }
                    ],
                }
            ],
        }
        report = build_report(snapshot, None)
        self.assertEqual(len(report["services"]), 1)
        self.assertFalse(report["services"][0]["live_present"])
        self.assertEqual(report["services"][0]["frontend_name"], "f1")

    def test_build_apply_plan_sets_weight_change(self) -> None:
        desired = [
            VirtualService(
                proto="tcp",
                vip="127.0.0.1",
                port=80,
                scheduler="wrr",
                reals=[RealServer(ip="10.0.0.1", port=80, weight=2)],
            )
        ]
        live = LiveIpvsState(
            services=[
                VirtualService(
                    proto="tcp",
                    vip="127.0.0.1",
                    port=80,
                    scheduler="wrr",
                    reals=[RealServer(ip="10.0.0.1", port=80, weight=1)],
                )
            ]
        )
        plan = build_apply_plan(desired, live)
        self.assertEqual(len(plan.set_reals), 1)

    def test_build_apply_plan_sets_proxy_method_change(self) -> None:
        desired = [
            VirtualService(
                proto="tcp",
                vip="127.0.0.1",
                port=80,
                scheduler="wrr",
                reals=[RealServer(ip="10.0.0.1", port=80, weight=1, method="nat")],
            )
        ]
        live = LiveIpvsState(
            services=[
                VirtualService(
                    proto="tcp",
                    vip="127.0.0.1",
                    port=80,
                    scheduler="wrr",
                    reals=[RealServer(ip="10.0.0.1", port=80, weight=1, method="routing")],
                )
            ]
        )
        plan = build_apply_plan(desired, live)
        self.assertEqual(len(plan.set_reals), 1)

    def test_desired_services_maps_snapshot(self) -> None:
        snapshot = {
            "groups": [
                {
                    "services": [
                        {
                            "proto": "udp",
                            "vip": "127.0.0.2",
                            "port": 53,
                            "scheduler": "wrr",
                            "reals": [{"ip": "10.0.0.2", "port": 53, "weight": 1}],
                        }
                    ]
                }
            ]
        }
        services = desired_services(snapshot)
        self.assertEqual(len(services), 1)
        self.assertEqual(services[0].proto, "udp")

    def test_build_report_matches_hostname_and_ip_vip(self) -> None:
        snapshot = {
            "groups": [
                {
                    "group": "g1",
                    "services": [
                        {
                            "group": "g1",
                            "frontend_name": "f1",
                            "proto": "tcp",
                            "vip": "localhost",
                            "port": 80,
                            "scheduler": "wrr",
                            "reals": [{"ip": "10.0.0.1", "port": 80, "weight": 1}],
                        }
                    ],
                }
            ]
        }
        live = LiveIpvsState(
            services=[
                VirtualService(
                    proto="tcp",
                    vip="127.0.0.1",
                    port=80,
                    scheduler="wrr",
                    reals=[],
                )
            ]
        )
        report = build_report(snapshot, live)
        self.assertTrue(report["services"][0]["live_present"])


if __name__ == "__main__":
    unittest.main()
