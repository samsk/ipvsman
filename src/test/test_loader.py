"""Tests for loader."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.loader import load_snapshot


class LoaderTest(unittest.TestCase):
    def test_load_snapshot_basic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "groups").mkdir()
            (root / "backends").mkdir()
            (root / "backend-maps").mkdir()
            (root / "check-refs").mkdir()
            (root / "groups" / "a.yaml").write_text(
                """
- group: dns
  vip: 127.0.0.1
  scheduler: wrr
  frontends:
    - name: dns
      proto: udp
      port: 53
  backends:
    - ip: 10.0.0.1
      weight: 1
  healthcheck:
    type: tcp
""",
                encoding="utf-8",
            )
            snap = load_snapshot(root)
            self.assertEqual(len(snap["groups"]), 1)
            svc = snap["groups"][0]["services"][0]
            self.assertEqual(svc["vip"], "127.0.0.1")
            self.assertEqual(svc["reals"][0]["ip"], "10.0.0.1")
            self.assertEqual(svc["reals"][0]["method"], "routing")

    def test_duplicate_group_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for d in ("groups", "backends", "backend-maps", "check-refs"):
                (root / d).mkdir()
            (root / "groups" / "a.yaml").write_text(
                """
- group: dup
  vip: 127.0.0.1
  frontends: [{name: a, proto: tcp, port: 80}]
  backends: [{ip: 10.0.0.1, weight: 1}]
  healthcheck: {type: tcp}
""",
                encoding="utf-8",
            )
            (root / "groups" / "b.yaml").write_text(
                """
- group: dup
  vip: 127.0.0.2
  frontends: [{name: b, proto: tcp, port: 81}]
  backends: [{ip: 10.0.0.2, weight: 1}]
  healthcheck: {type: tcp}
""",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_snapshot(root)

    def test_port_map_and_service_name_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for d in ("groups", "backends", "backend-maps", "check-refs"):
                (root / d).mkdir()
            (root / "groups" / "a.yaml").write_text(
                """
- group: g
  vip: 127.0.0.1
  frontends:
    - name: dns
      proto: udp
      port: domain
  backends:
    - ip: 10.0.0.1
      weight: 2
      port_map:
        "*": 5353
  healthcheck:
    type: tcp
""",
                encoding="utf-8",
            )
            snap = load_snapshot(root)
            svc = snap["groups"][0]["services"][0]
            self.assertEqual(svc["port"], 53)
            self.assertEqual(svc["reals"][0]["port"], 5353)
            self.assertGreaterEqual(snap["loaded_files_count"], 1)
            self.assertGreater(snap["config_version_mtime"], 0.0)

    def test_backend_file_path_traversal_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for d in ("groups", "backends", "backend-maps", "check-refs"):
                (root / d).mkdir()
            (root / "groups" / "a.yaml").write_text(
                """
- group: g
  vip: 127.0.0.1
  frontends:
    - name: f
      proto: tcp
      port: 80
  backend_files:
    - ../outside.yaml
  healthcheck:
    type: tcp
""",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_snapshot(root)

    def test_port_map_protocol_key_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for d in ("groups", "backends", "backend-maps", "check-refs"):
                (root / d).mkdir()
            (root / "groups" / "a.yaml").write_text(
                """
- group: g
  vip: 127.0.0.1
  frontends:
    - name: dns-udp
      proto: udp
      port: 53
    - name: dns-tcp
      proto: tcp
      port: 53
  backends:
    - ip: 10.0.0.1
      weight: 1
      port_map:
        "53/udp": 5353
        "53/tcp": 5354
  healthcheck:
    type: tcp
""",
                encoding="utf-8",
            )
            snap = load_snapshot(root)
            services = snap["groups"][0]["services"]
            ports = {(svc["proto"], svc["reals"][0]["port"]) for svc in services}
            self.assertIn(("udp", 5353), ports)
            self.assertIn(("tcp", 5354), ports)

    def test_port_map_list_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for d in ("groups", "backends", "backend-maps", "check-refs"):
                (root / d).mkdir()
            (root / "groups" / "a.yaml").write_text(
                """
- group: g
  vip: 127.0.0.1
  frontends:
    - name: dns-udp
      proto: udp
      port: 53
    - name: dns-tcp
      proto: tcp
      port: 53
  backends:
    - ip: 10.0.0.1
      weight: 1
      port_map:
        - "53/udp": 5353
        - "53/tcp": 5354
  healthcheck:
    type: tcp
""",
                encoding="utf-8",
            )
            snap = load_snapshot(root)
            services = snap["groups"][0]["services"]
            ports = {(svc["proto"], svc["reals"][0]["port"]) for svc in services}
            self.assertIn(("udp", 5353), ports)
            self.assertIn(("tcp", 5354), ports)

    def test_duplicate_virtual_service_emits_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for d in ("groups", "backends", "backend-maps", "check-refs"):
                (root / d).mkdir()
            (root / "groups" / "a.yaml").write_text(
                """
- group: g
  vip: 127.0.0.1
  frontends:
    - name: f1
      proto: tcp
      port: 80
    - name: f2
      proto: tcp
      port: 80
  backends:
    - ip: 10.0.0.1
      weight: 1
  healthcheck:
    type: tcp
""",
                encoding="utf-8",
            )
            snap = load_snapshot(root)
            self.assertTrue(any("duplicate virtual service" in w for w in snap.get("warnings", [])))

    def test_hostname_backend_expands_to_multiple_reals_with_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for d in ("groups", "backends", "backend-maps", "check-refs"):
                (root / d).mkdir()
            (root / "groups" / "a.yaml").write_text(
                """
- group: g
  vip: 127.0.0.1
  frontends:
    - name: f
      proto: tcp
      port: 80
  backends:
    - ip: example.backend.local
      weight: 1
  healthcheck:
    type: tcp
""",
                encoding="utf-8",
            )
            fake_info = [
                (2, 1, 6, "", ("10.0.0.10", 0)),
                (2, 1, 6, "", ("10.0.0.11", 0)),
            ]
            with patch("src.loader.socket.getaddrinfo", return_value=fake_info):
                snap = load_snapshot(root)
            reals = snap["groups"][0]["services"][0]["reals"]
            ips = {row["ip"] for row in reals}
            self.assertEqual(ips, {"10.0.0.10", "10.0.0.11"})
            self.assertTrue(any("resolved multiple IPs" in w for w in snap.get("warnings", [])))


if __name__ == "__main__":
    unittest.main()
