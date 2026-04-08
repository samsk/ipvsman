"""Tests for Prometheus metrics."""

from __future__ import annotations

import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

from src.metrics import MetricsServer, generate_metrics_body
from src.state import RuntimeState


class MetricsTest(unittest.TestCase):
    def test_generate_metrics_body_contains_gauges(self) -> None:
        try:
            import prometheus_client  # noqa: F401
        except ModuleNotFoundError:
            self.skipTest("prometheus_client not installed")
        state = RuntimeState()
        state.config_version_mtime = 1.5
        state.loaded_files_count = 3
        state.desired_generation = 7
        body, content_type = generate_metrics_body(state)
        self.assertIn(b"ipvsman_desired_generation", body)
        self.assertIn(b"ipvsman_config_version_mtime_seconds", body)
        self.assertIn(b"ipvsman_backend_ip_change_events_total", body)
        self.assertIn(b"ipvsman_backend_resolve_errors_total", body)
        self.assertIn("text/plain", content_type)

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
