"""Tests for HTTP API server."""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from src.api import ApiServer
from src.ipvs_exec import LiveIpvsState
from src.state import RuntimeState


class ApiServerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        for d in ("groups", "backends", "backend-maps", "check-refs"):
            (root / d).mkdir()
        self.config_dir = root
        self.state = RuntimeState()
        self.state.desired_snapshot = {
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
                            "healthcheck": {"type": "tcp", "interval": 1, "timeout": 1, "rise": 1, "fall": 1},
                            "reals": [{"ip": "10.0.0.1", "port": 80, "weight": 1, "check_target": {"ip": "10.0.0.1", "port": 80, "type": "tcp"}}],
                        }
                    ],
                }
            ],
            "desired_generation": 3,
            "config_version_mtime": 123.0,
            "live_state": LiveIpvsState(),
        }

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _get_json(self, base: str, path: str, token: str | None = None) -> dict:
        req = Request(f"{base}{path}")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        with urlopen(req, timeout=2) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def test_read_endpoints_without_auth(self) -> None:
        api = ApiServer(
            state=self.state,
            config_dir=self.config_dir,
            host="127.0.0.1",
            port=0,
            token=None,
            enable_write=False,
            max_body_bytes=4096,
        )
        api.start()
        try:
            host, port = api._http.server_address  # type: ignore[union-attr]
            base = f"http://{host}:{port}"
            self.assertIn("services", self._get_json(base, "/v1/services"))
            self.assertIn("frontends", self._get_json(base, "/v1/frontends"))
            self.assertIn("backends", self._get_json(base, "/v1/backends"))
            self.assertIn("healthchecks", self._get_json(base, "/v1/healthchecks"))
            self.assertIn("services", self._get_json(base, "/v1/status/detailed"))
            with urlopen(f"{base}/openapi.json", timeout=2) as resp:
                self.assertEqual(resp.status, 200)
        finally:
            api.stop()

    def test_readonly_auth_required(self) -> None:
        api = ApiServer(
            state=self.state,
            config_dir=self.config_dir,
            host="127.0.0.1",
            port=0,
            token="secret",
            enable_write=False,
            max_body_bytes=4096,
        )
        api.start()
        try:
            host, port = api._http.server_address  # type: ignore[union-attr]
            base = f"http://{host}:{port}"
            with self.assertRaises(HTTPError) as ctx:
                self._get_json(base, "/v1/services")
            self.assertEqual(ctx.exception.code, 401)
            payload = self._get_json(base, "/v1/services", token="secret")
            self.assertIn("services", payload)
        finally:
            api.stop()

    def test_metrics_path_exposed_when_enabled(self) -> None:
        api = ApiServer(
            state=self.state,
            config_dir=self.config_dir,
            host="127.0.0.1",
            port=0,
            token=None,
            enable_write=False,
            max_body_bytes=4096,
            enable_metrics=True,
        )
        api.start()
        try:
            host, port = api._http.server_address  # type: ignore[union-attr]
            try:
                with urlopen(f"http://{host}:{port}/metrics", timeout=2) as resp:
                    body = resp.read().decode("utf-8")
                    self.assertEqual(resp.status, 200)
                    self.assertIn("ipvsman_build_info", body)
            except HTTPError as exc:
                self.assertEqual(exc.code, 503)
        finally:
            api.stop()

    def test_metrics_openmetrics_content_type_when_accepted(self) -> None:
        api = ApiServer(
            state=self.state,
            config_dir=self.config_dir,
            host="127.0.0.1",
            port=0,
            token=None,
            enable_write=False,
            max_body_bytes=4096,
            enable_metrics=True,
        )
        api.start()
        try:
            host, port = api._http.server_address  # type: ignore[union-attr]
            req = Request(
                f"http://{host}:{port}/metrics",
                headers={"Accept": "application/openmetrics-text"},
            )
            try:
                with urlopen(req, timeout=2) as resp:
                    body = resp.read()
                    self.assertEqual(resp.status, 200)
                    self.assertIn("application/openmetrics-text", resp.headers.get("Content-Type", ""))
                    self.assertIn(b"# EOF\n", body)
            except HTTPError as exc:
                self.assertEqual(exc.code, 503)
        finally:
            api.stop()

    def test_metrics_path_not_exposed_when_disabled(self) -> None:
        api = ApiServer(
            state=self.state,
            config_dir=self.config_dir,
            host="127.0.0.1",
            port=0,
            token=None,
            enable_write=False,
            max_body_bytes=4096,
            enable_metrics=False,
        )
        api.start()
        try:
            host, port = api._http.server_address  # type: ignore[union-attr]
            with self.assertRaises(HTTPError) as ctx:
                urlopen(f"http://{host}:{port}/metrics", timeout=2)
            self.assertEqual(ctx.exception.code, 404)
        finally:
            api.stop()

    def test_put_rejected_when_write_disabled(self) -> None:
        api = ApiServer(
            state=self.state,
            config_dir=self.config_dir,
            host="127.0.0.1",
            port=0,
            token="secret",
            enable_write=False,
            max_body_bytes=4096,
        )
        api.start()
        try:
            host, port = api._http.server_address  # type: ignore[union-attr]
            base = f"http://{host}:{port}"
            req = Request(f"{base}/v1/config", method="PUT", data=b"{}", headers={"Authorization": "Bearer secret"})
            with self.assertRaises(HTTPError) as ctx:
                urlopen(req, timeout=2)
            self.assertEqual(ctx.exception.code, 403)
        finally:
            api.stop()

    def test_put_unauthorized_without_token(self) -> None:
        api = ApiServer(
            state=self.state,
            config_dir=self.config_dir,
            host="127.0.0.1",
            port=0,
            token="secret",
            enable_write=True,
            max_body_bytes=4096,
        )
        api.start()
        try:
            host, port = api._http.server_address  # type: ignore[union-attr]
            base = f"http://{host}:{port}"
            req = Request(f"{base}/v1/config", method="PUT", data=b"{}", headers={})
            with self.assertRaises(HTTPError) as ctx:
                urlopen(req, timeout=2)
            self.assertEqual(ctx.exception.code, 401)
        finally:
            api.stop()

    def test_put_config_success(self) -> None:
        api = ApiServer(
            state=self.state,
            config_dir=self.config_dir,
            host="127.0.0.1",
            port=0,
            token="secret",
            enable_write=True,
            max_body_bytes=4096,
        )
        api.start()
        try:
            host, port = api._http.server_address  # type: ignore[union-attr]
            base = f"http://{host}:{port}"
            payload = {
                "groups": [
                    {
                        "group": "new-group",
                        "vip": "127.0.0.2",
                        "frontends": [{"name": "f", "proto": "tcp", "port": 8080}],
                        "backends": [{"ip": "10.0.0.20", "weight": 1}],
                        "healthcheck": {"type": "tcp"},
                    }
                ]
            }
            req = Request(
                f"{base}/v1/config",
                method="PUT",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Authorization": "Bearer secret",
                    "Content-Type": "application/json",
                },
            )
            with urlopen(req, timeout=2) as resp:
                self.assertEqual(resp.status, 200)
                out = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(out["ok"])
            target = self.config_dir / "groups" / "api-put.yaml"
            self.assertTrue(target.exists())
            self.assertIn("new-group", target.read_text(encoding="utf-8"))
            services = self._get_json(base, "/v1/services", token="secret")["services"]
            self.assertTrue(any(s["group"] == "new-group" for s in services))
            detailed = self._get_json(base, "/v1/status/detailed", token="secret")
            self.assertIn("services", detailed)
        finally:
            api.stop()

    def test_put_config_invalid_content_length(self) -> None:
        api = ApiServer(
            state=self.state,
            config_dir=self.config_dir,
            host="127.0.0.1",
            port=0,
            token="secret",
            enable_write=True,
            max_body_bytes=4096,
        )
        api.start()
        try:
            host, port = api._http.server_address  # type: ignore[union-attr]
            base = f"http://{host}:{port}"
            req = Request(
                f"{base}/v1/config",
                method="PUT",
                data=b"{}",
                headers={
                    "Authorization": "Bearer secret",
                    "Content-Type": "application/json",
                    "Content-Length": "oops",
                },
            )
            with self.assertRaises(HTTPError) as ctx:
                urlopen(req, timeout=2)
            self.assertEqual(ctx.exception.code, 400)
        finally:
            api.stop()

    def test_concurrent_snapshot_swaps_keep_get_stable(self) -> None:
        api = ApiServer(
            state=self.state,
            config_dir=self.config_dir,
            host="127.0.0.1",
            port=0,
            token=None,
            enable_write=False,
            max_body_bytes=4096,
        )
        api.start()
        stop = threading.Event()

        def writer() -> None:
            i = 0
            while not stop.is_set():
                with self.state.lock:
                    self.state.desired_snapshot = {
                        "groups": [
                            {
                                "group": f"g{i % 2}",
                                "services": [
                                    {
                                        "group": f"g{i % 2}",
                                        "frontend_name": "f1",
                                        "proto": "tcp",
                                        "vip": "127.0.0.1",
                                        "port": 80,
                                        "scheduler": "wrr",
                                        "healthcheck": {"type": "tcp", "interval": 1, "timeout": 1, "rise": 1, "fall": 1},
                                        "reals": [{"ip": "10.0.0.1", "port": 80, "weight": 1, "check_target": {"ip": "10.0.0.1", "port": 80, "type": "tcp"}}],
                                    }
                                ],
                            }
                        ],
                        "desired_generation": i,
                        "config_version_mtime": float(i),
                        "live_state": LiveIpvsState(),
                    }
                i += 1
                time.sleep(0.005)

        t = threading.Thread(target=writer, daemon=True)
        t.start()
        try:
            host, port = api._http.server_address  # type: ignore[union-attr]
            base = f"http://{host}:{port}"
            for _ in range(30):
                out = self._get_json(base, "/v1/status/detailed")
                self.assertIn("services", out)
        finally:
            stop.set()
            t.join(timeout=1)
            api.stop()

    def test_rate_limit_returns_429(self) -> None:
        api = ApiServer(
            state=self.state,
            config_dir=self.config_dir,
            host="127.0.0.1",
            port=0,
            token=None,
            enable_write=False,
            max_body_bytes=4096,
            rate_limit_per_minute=2,
        )
        api.start()
        try:
            host, port = api._http.server_address  # type: ignore[union-attr]
            base = f"http://{host}:{port}"
            self._get_json(base, "/v1/services")
            self._get_json(base, "/v1/services")
            with self.assertRaises(HTTPError) as ctx:
                self._get_json(base, "/v1/services")
            self.assertEqual(ctx.exception.code, 429)
        finally:
            api.stop()


if __name__ == "__main__":
    unittest.main()
