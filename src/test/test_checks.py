"""Tests for health checks."""

from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from src import checks
from src.check_runtime import commit_next_check, is_check_due, make_backend_health_key, should_schedule_check, update_health_state
from src.constants import HEALTH_HEALTHY, HEALTH_UNHEALTHY
from src.models import CheckTarget, HealthCheck, RuntimeCheckResult


class CheckStateTest(unittest.TestCase):
    def test_rise_fall(self) -> None:
        prev = RuntimeCheckResult(
            state=HEALTH_UNHEALTHY,
            ready=True,
            fail_count=2,
            success_count=0,
            changed_at=0.0,
            updated_at=0.0,
        )
        nxt = update_health_state(prev, ok=True, now=1.0, rise=1, fall=2, message="ok")
        self.assertEqual(nxt.state, HEALTH_HEALTHY)

    def test_should_schedule_check_respects_interval(self) -> None:
        next_due: dict[str, float] = {}
        self.assertTrue(should_schedule_check(next_due, "g1|10.0.0.1", now=10.0, interval=5.0))
        self.assertFalse(should_schedule_check(next_due, "g1|10.0.0.1", now=12.0, interval=5.0))
        self.assertTrue(should_schedule_check(next_due, "g1|10.0.0.1", now=15.1, interval=5.0))

    def test_is_check_due_and_commit(self) -> None:
        next_due: dict[str, float] = {}
        self.assertTrue(is_check_due(next_due, "k", now=1.0, interval=1.0))
        commit_next_check(next_due, "k", now=1.0, interval=10.0)
        self.assertFalse(is_check_due(next_due, "k", now=5.0, interval=10.0))

    def test_make_backend_health_key_uses_frontend_and_port(self) -> None:
        key_a = make_backend_health_key("g1", "f-http", "10.0.0.1", 80)
        key_b = make_backend_health_key("g1", "f-https", "10.0.0.1", 443)
        self.assertNotEqual(key_a, key_b)

    def test_dns_check_requires_query_name(self) -> None:
        target = CheckTarget(ip="127.0.0.1", port=53, type="dns")
        hc = HealthCheck(type="dns", interval=1.0, timeout=1.0, rise=1, fall=1)
        ok, message = checks.dns_check(target, hc)
        self.assertFalse(ok)
        self.assertIn("query_name", message)

    def test_dns_check_reports_missing_dependency(self) -> None:
        target = CheckTarget(ip="127.0.0.1", port=53, type="dns", query_name="example.org")
        hc = HealthCheck(type="dns", interval=1.0, timeout=1.0, rise=1, fall=1)
        with patch("src.checks._load_dns_resolver_module", side_effect=ModuleNotFoundError("dns")):
            ok, message = checks.dns_check(target, hc)
        self.assertFalse(ok)
        self.assertIn("dnspython", message)

    def test_dns_check_queries_target_server(self) -> None:
        resolver_instance = Mock()
        class _Answer:
            rrset = [object()]

            def __len__(self) -> int:
                return 1

        answer = _Answer()
        resolver_instance.resolve.return_value = answer
        resolver_cls = Mock(return_value=resolver_instance)
        dns_resolver = Mock(Resolver=resolver_cls)

        target = CheckTarget(ip="10.0.0.53", port=5353, type="dns", query_name="example.org", query_type="A")
        hc = HealthCheck(type="dns", interval=1.0, timeout=2.5, rise=1, fall=1)

        with patch("src.checks._load_dns_resolver_module", return_value=dns_resolver):
            ok, message = checks.dns_check(target, hc)

        self.assertTrue(ok)
        self.assertIn("dns ok", message)
        self.assertEqual(resolver_instance.nameservers, ["10.0.0.53"])
        self.assertEqual(resolver_instance.port, 5353)
        self.assertEqual(resolver_instance.timeout, 2.5)
        self.assertEqual(resolver_instance.lifetime, 2.5)
        resolver_instance.resolve.assert_called_once_with("example.org", "A", tcp=False, raise_on_no_answer=False)

    def test_dns_check_uses_group_defaults(self) -> None:
        resolver_instance = Mock()

        class _Answer:
            rrset = [object()]

            def __len__(self) -> int:
                return 1

        resolver_instance.resolve.return_value = _Answer()
        resolver_cls = Mock(return_value=resolver_instance)
        dns_resolver = Mock(Resolver=resolver_cls)

        target = CheckTarget(ip="10.0.0.53", port=53, type="dns")
        hc = HealthCheck(type="dns", interval=1.0, timeout=2.0, rise=1, fall=1, query_name="example.net", query_type="TXT")

        with patch("src.checks._load_dns_resolver_module", return_value=dns_resolver):
            ok, _message = checks.dns_check(target, hc)

        self.assertTrue(ok)
        resolver_instance.resolve.assert_called_once_with("example.net", "TXT", tcp=False, raise_on_no_answer=False)


if __name__ == "__main__":
    unittest.main()
