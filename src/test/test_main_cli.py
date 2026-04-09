"""CLI smoke tests."""

from __future__ import annotations

import tempfile
import unittest
import io
import signal
import subprocess
from pathlib import Path
from unittest.mock import Mock, patch
from contextlib import redirect_stdout

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

    def test_dump_runs_without_config_load(self) -> None:
        output = io.StringIO()
        with (
            patch("src.main.ipvs_exec.read_dump", return_value="-A -t 127.0.0.1:80 -s wrr\n") as mock_dump,
            patch("src.main.load_snapshot") as mock_load,
            redirect_stdout(output),
        ):
            rc = main(["--dump", "--no-syslog"])
        self.assertEqual(rc, 0)
        self.assertEqual(output.getvalue(), "-A -t 127.0.0.1:80 -s wrr\n")
        mock_dump.assert_called_once()
        mock_load.assert_not_called()

    def test_dump_failure_returns_2(self) -> None:
        with (
            patch("src.main.ipvs_exec.read_dump", side_effect=RuntimeError("boom")),
            patch("src.main.load_snapshot") as mock_load,
        ):
            rc = main(["--dump", "--no-syslog"])
        self.assertEqual(rc, 2)
        mock_load.assert_not_called()

    def test_reload_validates_and_signals_pid_hint(self) -> None:
        with (
            patch("src.main.load_snapshot", return_value={"groups": []}) as mock_load,
            patch("src.main.os.kill") as mock_kill,
            patch("src.main.read_lock_pid") as mock_lock_pid,
        ):
            rc = main(["--reload", "--pid", "1234", "--no-syslog"])
        self.assertEqual(rc, 0)
        mock_load.assert_called_once()
        mock_kill.assert_called_once_with(1234, signal.SIGHUP)
        mock_lock_pid.assert_not_called()

    def test_reload_reads_lock_pid_when_hint_missing(self) -> None:
        with (
            patch("src.main.load_snapshot", return_value={"groups": []}),
            patch("src.main.read_lock_pid", return_value=5678) as mock_lock_pid,
            patch("src.main.os.kill") as mock_kill,
        ):
            rc = main(["--reload", "--no-syslog"])
        self.assertEqual(rc, 0)
        mock_lock_pid.assert_called_once()
        mock_kill.assert_called_once_with(5678, signal.SIGHUP)

    def test_reload_fails_when_validation_fails(self) -> None:
        with (
            patch("src.main.load_snapshot", side_effect=RuntimeError("invalid")),
            patch("src.main.os.kill") as mock_kill,
        ):
            rc = main(["--reload", "--pid", "1234", "--no-syslog"])
        self.assertEqual(rc, 2)
        mock_kill.assert_not_called()

    def test_reload_fails_when_pid_missing(self) -> None:
        with (
            patch("src.main.load_snapshot", return_value={"groups": []}),
            patch("src.main.read_lock_pid", return_value=None),
            patch("src.main.os.kill") as mock_kill,
        ):
            rc = main(["--reload", "--no-syslog"])
        self.assertEqual(rc, 2)
        mock_kill.assert_not_called()

    def test_stats_prints_table_without_config_load(self) -> None:
        stats_out = """IP Virtual Server version 1.2.1 (size=4096)
Prot LocalAddress:Port               Conns   InPkts  OutPkts  InBytes OutBytes
  -> RemoteAddress:Port
TCP  95.217.145.226:53                  70      226       26    13588     1236
  -> 192.168.13.201:53                  52      208        8    12508      516
"""
        cp = subprocess.CompletedProcess(args=["ipvsadm", "-ln", "--stats"], returncode=0, stdout=stats_out, stderr="")
        output = io.StringIO()
        with (
            patch("src.main.ipvs_exec._run", return_value=cp),
            patch("src.main.load_snapshot") as mock_load,
            redirect_stdout(output),
        ):
            rc = main(["--stats", "--no-syslog"])
        self.assertEqual(rc, 0)
        out = output.getvalue()
        self.assertIn("TYPE", out)
        self.assertIn("PROTO", out)
        self.assertIn("NAME", out)
        self.assertIn("svc", out)
        self.assertIn("rs", out)
        self.assertIn("95.217.145.226:53", out)
        mock_load.assert_not_called()


if __name__ == "__main__":
    unittest.main()
