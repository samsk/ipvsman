"""Tests for Prometheus metrics."""

from __future__ import annotations

import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

from src.metrics import MetricsServer, generate_metrics_body
from src.ipvs_exec import IpvsStatsSnapshot, RealServer, VirtualService
from src.state import RuntimeState
from src.models import RuntimeCheckResult


class MetricsTest(unittest.TestCase):
    def setUp(self) -> None:
        # reset shared stats cache
        import src.metrics as metrics_mod

        metrics_mod._ipvs_stats_cache_value = None
        metrics_mod._ipvs_stats_cache_at = 0.0
        metrics_mod._ipvs_stats_scrape_failures = 0

    def test_generate_metrics_body_contains_gauges(self) -> None:
        try:
            import prometheus_client  # noqa: F401
        except ModuleNotFoundError:
            self.skipTest("prometheus_client not installed")
        state = RuntimeState()
        state.config_version_mtime = 1.5
        state.loaded_files_count = 3
        state.desired_generation = 7
        body, content_type = generate_metrics_body(state, include_ipvs_stats=False)
        self.assertIn(b"ipvsman_desired_generation", body)
        self.assertIn(b"ipvsman_config_version_mtime_seconds", body)
        self.assertIn(b"ipvsman_backend_ip_change_events_total", body)
        self.assertIn(b"ipvsman_backend_resolve_error_events_total", body)
        self.assertIn("text/plain", content_type)

    def test_generate_metrics_body_contains_ipvs_stats_when_enabled(self) -> None:
        try:
            import prometheus_client  # noqa: F401
        except ModuleNotFoundError:
            self.skipTest("prometheus_client not installed")
        state = RuntimeState()
        state.desired_snapshot = {
            "groups": [
                {
                    "services": [
                        {
                            "group": "g1",
                            "frontend_name": "f1",
                            "proto": "tcp",
                            "vip": "1.1.1.1",
                            "port": 80,
                            "reals": [
                                {
                                    "address": "isp-dns-02.vm.dc2.in.dob.sk",
                                    "ip": "10.0.0.1",
                                    "port": 80,
                                }
                            ],
                        }
                    ]
                }
            ]
        }
        fake = IpvsStatsSnapshot(
            services=[
                VirtualService(
                    proto="tcp",
                    vip="1.1.1.1",
                    port=80,
                    scheduler="wrr",
                    conns=10,
                    inpkts=20,
                    outpkts=30,
                    inbytes=40,
                    outbytes=50,
                    reals=[
                        RealServer(
                            ip="10.0.0.1",
                            port=80,
                            weight=1,
                            active_conn=3,
                            inpkts=4,
                            outpkts=5,
                            inbytes=6,
                            outbytes=7,
                        )
                    ],
                )
            ]
        )
        with patch("src.metrics.ipvs_exec.read_stats", return_value=fake):
            body, _ = generate_metrics_body(state, include_ipvs_stats=True)
        self.assertIn(b"ipvsman_ipvs_service_connections", body)
        self.assertIn(b'group="g1"', body)
        self.assertIn(b'frontend="f1"', body)
        self.assertNotIn(b'vip="1.1.1.1"', body)
        self.assertIn(b'address="isp-dns-02.vm.dc2.in.dob.sk"', body)

    def test_generate_metrics_body_contains_healthcheck_metrics_configured(self) -> None:
        try:
            import prometheus_client  # noqa: F401
        except ModuleNotFoundError:
            self.skipTest("prometheus_client not installed")
        state = RuntimeState()
        state.desired_snapshot = {
            "groups": [
                {
                    "services": [
                        {
                            "group": "g1",
                            "frontend_name": "f1",
                            "proto": "tcp",
                            "vip": "1.1.1.1",
                            "port": 80,
                            "reals": [{"address": "node-1.example", "ip": "10.0.0.1", "port": 80}],
                        }
                    ]
                }
            ]
        }
        state.health_cache["g1|f1|10.0.0.1|80"] = RuntimeCheckResult(
            state=1,
            ready=True,
            fail_count=0,
            success_count=1,
            changed_at=1.0,
            updated_at=2.0,
            message="ok",
        )
        body, _ = generate_metrics_body(state, include_ipvs_stats=False, include_healthchecks=True, ipvs_stats_labels_mode="configured")
        self.assertIn(b"ipvsman_healthcheck_state", body)
        self.assertIn(b'group="g1"', body)
        self.assertIn(b'frontend="f1"', body)
        self.assertIn(b'address="node-1.example"', body)

    def test_generate_metrics_body_omits_healthcheck_metrics_when_disabled(self) -> None:
        try:
            import prometheus_client  # noqa: F401
        except ModuleNotFoundError:
            self.skipTest("prometheus_client not installed")
        state = RuntimeState()
        state.health_cache["g1|f1|10.0.0.1|80"] = RuntimeCheckResult(
            state=1,
            ready=True,
            fail_count=0,
            success_count=1,
            changed_at=1.0,
            updated_at=2.0,
            message="ok",
        )
        body, _ = generate_metrics_body(state, include_ipvs_stats=False, include_healthchecks=False)
        self.assertNotIn(b"ipvsman_healthcheck_state", body)

    def test_generate_metrics_body_route_labels_mode(self) -> None:
        try:
            import prometheus_client  # noqa: F401
        except ModuleNotFoundError:
            self.skipTest("prometheus_client not installed")
        state = RuntimeState()
        fake = IpvsStatsSnapshot(services=[VirtualService(proto="tcp", vip="1.1.1.1", port=80, scheduler="wrr", conns=10, reals=[])])
        with patch("src.metrics.ipvs_exec.read_stats", return_value=fake):
            body, _ = generate_metrics_body(state, include_ipvs_stats=True, ipvs_stats_labels_mode="route")
        self.assertIn(b'vip="1.1.1.1"', body)

    def test_generate_metrics_body_both_labels_mode(self) -> None:
        try:
            import prometheus_client  # noqa: F401
        except ModuleNotFoundError:
            self.skipTest("prometheus_client not installed")
        state = RuntimeState()
        state.desired_snapshot = {
            "groups": [
                {
                    "services": [
                        {
                            "group": "g1",
                            "frontend_name": "f1",
                            "proto": "tcp",
                            "vip": "1.1.1.1",
                            "port": 80,
                        }
                    ]
                }
            ]
        }
        fake = IpvsStatsSnapshot(services=[VirtualService(proto="tcp", vip="1.1.1.1", port=80, scheduler="wrr", conns=10, reals=[])])
        with patch("src.metrics.ipvs_exec.read_stats", return_value=fake):
            body, _ = generate_metrics_body(state, include_ipvs_stats=True, ipvs_stats_labels_mode="both")
        self.assertIn(b"ipvsman_ipvs_service_connections_route", body)
        self.assertIn(b'group="g1"', body)

    def test_generate_metrics_body_uses_stats_cache_for_one_second(self) -> None:
        try:
            import prometheus_client  # noqa: F401
        except ModuleNotFoundError:
            self.skipTest("prometheus_client not installed")
        state = RuntimeState()
        fake = IpvsStatsSnapshot(
            services=[
                VirtualService(
                    proto="tcp",
                    vip="1.1.1.1",
                    port=80,
                    scheduler="wrr",
                    conns=1,
                    reals=[],
                )
            ]
        )
        with (
            patch("src.metrics.ipvs_exec.read_stats", return_value=fake) as mock_read,
            patch("src.metrics.time.monotonic", side_effect=[100.0, 100.2]),
        ):
            generate_metrics_body(state, include_ipvs_stats=True)
            generate_metrics_body(state, include_ipvs_stats=True)
        self.assertEqual(mock_read.call_count, 1)

    def test_generate_metrics_body_increments_stats_failure_counter(self) -> None:
        try:
            import prometheus_client  # noqa: F401
        except ModuleNotFoundError:
            self.skipTest("prometheus_client not installed")
        state = RuntimeState()
        with patch("src.metrics.ipvs_exec.read_stats", side_effect=RuntimeError("ipvsadm failed")):
            body, _ = generate_metrics_body(state, include_ipvs_stats=True)
        self.assertIn(b"ipvsman_ipvs_stats_scrape_failures_total", body)
        self.assertIn(b"ipvsman_ipvs_stats_scrape_failures_total 1.0", body)

    def test_metrics_server_503_when_generate_raises_module_not_found(self) -> None:
        state = RuntimeState()
        srv = MetricsServer(state, "127.0.0.1", 0)
        with patch("src.metrics.generate_metrics_body", side_effect=ModuleNotFoundError("prometheus_client")):
            srv.start()
            try:
                self.assertIsNotNone(srv._http)
                host, port = srv._http.server_address  # type: ignore[union-attr]
                try:
                    urlopen(f"http://{host}:{port}/metrics", timeout=2)
                except HTTPError as exc:
                    self.assertEqual(exc.code, 503)
                else:
                    self.fail("expected 503")
            finally:
                srv.stop()

    def test_generate_metrics_body_openmetrics_when_requested(self) -> None:
        try:
            import prometheus_client  # noqa: F401
        except ModuleNotFoundError:
            self.skipTest("prometheus_client not installed")
        state = RuntimeState()
        body, content_type = generate_metrics_body(state, openmetrics=True)
        self.assertIn("application/openmetrics-text", content_type)
        self.assertIn(b"# EOF\n", body)


if __name__ == "__main__":
    unittest.main()
