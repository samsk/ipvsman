"""Health check implementations."""

from __future__ import annotations

import socket
from importlib import import_module
from typing import Any, Tuple
from urllib import request

from src.models import CheckTarget, HealthCheck


class _NoRedirect(request.HTTPRedirectHandler):
    """Disable redirects (SSRF hardening for config-driven URLs)."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


_http_opener = request.build_opener(_NoRedirect())


def tcp_check(target: CheckTarget, timeout: float) -> Tuple[bool, str]:
    """Check TCP reachability (IPv4 only).

    Input:
    - target: host/port to connect to.
    - timeout: connection timeout in seconds.

    Output:
    - Tuple(ok, message).
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((target.ip, target.port))
        return True, "tcp ok"
    except OSError as exc:
        return False, f"tcp failed: {exc}"
    finally:
        sock.close()


def http_check(target: CheckTarget, check: HealthCheck, use_tls: bool) -> Tuple[bool, str]:
    """Check HTTP/HTTPS endpoint status."""
    scheme = "https" if use_tls else "http"
    path = target.path or check.path or "/"
    host = target.host or check.host or target.ip
    url = f"{scheme}://{target.ip}:{target.port}{path}"
    req = request.Request(url, headers={"Host": host})
    try:
        with _http_opener.open(req, timeout=target.timeout or check.timeout) as resp:
            code = int(resp.status)
            ok = code == int(check.expected_status)
            return ok, f"http {code} ({url})"
    except Exception as exc:
        return False, f"http failed ({url}): {exc}"


def dns_check(target: CheckTarget, _check: HealthCheck) -> Tuple[bool, str]:
    """Run a DNS query against target DNS server.

    Input:
    - target: Probe target including dns server ip:port and optional query override.
    - _check: Group check config used for defaults.

    Output:
    - Tuple(ok, message).
    """
    query_name = target.query_name or _check.query_name
    query_type = (target.query_type or _check.query_type or "A").upper()
    timeout = float(target.timeout or _check.timeout or 3.0)
    if not query_name:
        return False, "dns failed: query_name is required"
    try:
        dns_resolver = _load_dns_resolver_module()
    except ModuleNotFoundError:
        return False, "dns failed: dnspython not installed"
    try:
        resolver = dns_resolver.Resolver(configure=False)
        resolver.nameservers = [target.ip]
        resolver.port = int(target.port)
        resolver.timeout = timeout
        resolver.lifetime = timeout
        answer = resolver.resolve(query_name, query_type, tcp=False, raise_on_no_answer=False)
        rrset = getattr(answer, "rrset", None)
        answer_count = len(answer) if rrset is not None else 0
        if answer_count > 0:
            return True, f"dns ok ({query_name} {query_type} answers={answer_count})"
        return False, f"dns failed ({query_name} {query_type}): no answer"
    except Exception as exc:
        return False, f"dns failed ({query_name} {query_type}): {exc}"


def _load_dns_resolver_module() -> Any:
    """Import and return dns.resolver module."""
    return import_module("dns.resolver")
