"""CLI smoke tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from src.main import main


class MainCliTest(unittest.TestCase):
    def test_version_mode(self) -> None:
        rc = main(["--version"])
        self.assertEqual(rc, 0)

    def test_requires_action_or_service(self) -> None:
        rc = main(["--no-syslog"])
        self.assertEqual(rc, 2)

    def test_service_mutex_with_test(self) -> None:
        rc = main(["--service", "--test", "--no-syslog"])
        self.assertEqual(rc, 2)

    def test_test_mode_ok(self) -> None:
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
    - ip: 10.0.0.1
      weight: 1
  healthcheck:
    type: tcp
""",
                encoding="utf-8",
            )
            rc = main(["--config-dir", str(root), "--test", "--no-syslog"])
            self.assertEqual(rc, 0)

    def test_test_mode_invalid_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for d in ("groups", "backends", "backend-maps", "check-refs"):
                (root / d).mkdir()
            (root / "groups" / "bad.yaml").write_text("not: [valid", encoding="utf-8")
            rc = main(["--config-dir", str(root), "--test", "--no-syslog"])
            self.assertEqual(rc, 2)

    def test_lock_failure_returns_2(self) -> None:
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
    - ip: 10.0.0.1
      weight: 1
  healthcheck:
    type: tcp
""",
                encoding="utf-8",
            )
            lock_file = root / "x.lock"
            from src.lock import ProcessLock

            holder = ProcessLock(lock_file)
            self.assertTrue(holder.acquire())
            try:
                rc = main(
                    [
                        "--config-dir",
                        str(root),
                        "--lock-file",
                        str(lock_file),
                        "--interval",
                        "0.1",
                        "--no-syslog",
                        "--service",
                    ]
                )
                self.assertEqual(rc, 2)
            finally:
                holder.release()

    def test_alert_when_api_non_localhost_without_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for d in ("groups", "backends", "backend-maps", "check-refs"):
                (root / d).mkdir()
            (root / "groups" / "bad.yaml").write_text("not: [valid", encoding="utf-8")
            fake_log = Mock()
            with patch("src.main.setup_logging", return_value=fake_log):
                rc = main(
                    [
                        "--config-dir",
                        str(root),
                        "--api-enable",
                        "--api-host",
                        "0.0.0.0",
                        "--test",
                    ]
                )
            self.assertEqual(rc, 2)
            fake_log.critical.assert_called_once()

    def test_disable_group_runs_one_shot_without_lock(self) -> None:
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
    - ip: 10.0.0.1
      weight: 1
  healthcheck:
    type: tcp
""",
                encoding="utf-8",
            )
            with (
                patch("src.main.ipvs_exec.read_live", return_value=Mock(services=[])),
                patch("src.main.ipvs_exec.apply_plan", return_value=Mock(ok=True, message="ok")) as mock_apply,
                patch("src.main.ProcessLock") as mock_lock,
            ):
                rc = main(["--config-dir", str(root), "--disable-group", "g", "--no-syslog"])
            self.assertEqual(rc, 0)
            mock_apply.assert_called_once()
            mock_lock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
