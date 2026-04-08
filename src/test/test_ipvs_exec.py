"""Tests for ipvs_exec module."""

from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from src.ipvs_exec import IpvsApplyPlan, RealServer, VirtualService, apply_plan, parse_ipvsadm_ln


class IpvsExecTest(unittest.TestCase):
    def test_parse_ipvsadm_ln_basic(self) -> None:
        output = """
IP Virtual Server version 1.2.1 (size=4096)
Prot LocalAddress:Port Scheduler Flags
  -> RemoteAddress:Port           Forward Weight ActiveConn InActConn
TCP  127.0.0.1:80 wrr
  -> 10.0.0.1:80                  Masq    2      0          0
UDP  127.0.0.2:53 rr
  -> 10.0.0.2:53                  Masq    1      0          0
"""
        state = parse_ipvsadm_ln(output)
        self.assertEqual(len(state.services), 2)
        self.assertEqual(state.services[0].proto, "tcp")
        self.assertEqual(state.services[0].reals[0].weight, 2)
        self.assertEqual(state.services[0].reals[0].method, "nat")

    def test_parse_ipvsadm_ln_ignores_bad_rows(self) -> None:
        output = "TCP 127.0.0.1:80 wrr\nbadrow\n"
        state = parse_ipvsadm_ln(output)
        self.assertEqual(len(state.services), 1)
        self.assertEqual(len(state.services[0].reals), 0)

    def test_apply_plan_executes_commands(self) -> None:
        svc = VirtualService(proto="tcp", vip="127.0.0.1", port=80, scheduler="wrr")
        rs = RealServer(ip="10.0.0.1", port=80, weight=1)
        plan = IpvsApplyPlan(add_services=[svc], add_reals=[(svc, rs)], set_reals=[(svc, rs)], del_reals=[(svc, rs)], del_services=[svc])
        calls: list[list[str]] = []

        def _fake_run(args: list[str], timeout: float = 10.0):  # type: ignore[no-untyped-def]
            calls.append(args)
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

        with patch("src.ipvs_exec._run", side_effect=_fake_run):
            result = apply_plan(plan)
        self.assertTrue(result.ok)
        self.assertEqual(len(calls), 5)
        # delete-service must not include scheduler (-s)
        self.assertEqual(calls[0][:4], ["ipvsadm", "-D", "-t", "127.0.0.1:80"])
        self.assertNotIn("-s", calls[0])
        self.assertIn("-g", calls[3])
        self.assertIn("-g", calls[4])

    def test_apply_plan_uses_nat_flag_for_nat_backend(self) -> None:
        svc = VirtualService(proto="udp", vip="127.0.0.2", port=53, scheduler="rr")
        rs = RealServer(ip="10.0.0.2", port=53, weight=1, method="nat")
        plan = IpvsApplyPlan(add_services=[svc], add_reals=[(svc, rs)])
        calls: list[list[str]] = []

        def _fake_run(args: list[str], timeout: float = 10.0):  # type: ignore[no-untyped-def]
            calls.append(args)
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

        with patch("src.ipvs_exec._run", side_effect=_fake_run):
            result = apply_plan(plan)
        self.assertTrue(result.ok)
        self.assertIn("-m", calls[1])

    def test_apply_plan_returns_error_on_nonzero_exit(self) -> None:
        svc = VirtualService(proto="tcp", vip="127.0.0.1", port=80, scheduler="wrr")
        plan = IpvsApplyPlan(add_services=[svc])
        failed = subprocess.CompletedProcess(
            args=["ipvsadm", "-A", "-t", "127.0.0.1:80", "-s", "wrr"],
            returncode=1,
            stdout="",
            stderr="permission denied",
        )
        with patch("src.ipvs_exec._run", return_value=failed):
            result = apply_plan(plan)
        self.assertFalse(result.ok)
        self.assertIn("permission denied", result.message)


if __name__ == "__main__":
    unittest.main()
