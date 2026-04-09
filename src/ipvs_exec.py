"""Single module for all ipvsadm interactions."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from typing import Iterable


@dataclass
class RealServer:
    """Real server model."""

    ip: str
    port: int
    weight: int
    method: str = "nat"
    active_conn: int = 0
    inactive_conn: int = 0
    inpkts: int = 0
    outpkts: int = 0
    inbytes: int = 0
    outbytes: int = 0


@dataclass
class VirtualService:
    """Virtual service model."""

    proto: str
    vip: str
    port: int
    scheduler: str
    conns: int = 0
    inpkts: int = 0
    outpkts: int = 0
    inbytes: int = 0
    outbytes: int = 0
    reals: list[RealServer] = field(default_factory=list)


@dataclass
class LiveIpvsState:
    """Live IPVS state."""

    services: list[VirtualService] = field(default_factory=list)


@dataclass
class IpvsStatsSnapshot:
    """IPVS stats snapshot."""

    services: list[VirtualService] = field(default_factory=list)


@dataclass
class IpvsApplyPlan:
    """Apply plan for IPVS."""

    add_services: list[VirtualService] = field(default_factory=list)
    del_services: list[VirtualService] = field(default_factory=list)
    add_reals: list[tuple[VirtualService, RealServer]] = field(default_factory=list)
    del_reals: list[tuple[VirtualService, RealServer]] = field(default_factory=list)
    set_reals: list[tuple[VirtualService, RealServer]] = field(default_factory=list)


@dataclass
class IpvsApplyResult:
    """Apply result."""

    ok: bool
    message: str = ""


@dataclass
class ManagedScope:
    """Managed service scope."""

    services: list[VirtualService] = field(default_factory=list)


def _run(args: Iterable[str], timeout: float = 10.0) -> subprocess.CompletedProcess[str]:
    """Run ipvsadm command."""
    return subprocess.run(
        list(args),
        check=False,
        text=True,
        capture_output=True,
        timeout=timeout,
    )


def parse_ipvsadm_ln(output: str) -> LiveIpvsState:
    """Parse `ipvsadm -ln` output."""
    state = LiveIpvsState()
    current: VirtualService | None = None
    for line in output.splitlines():
        s = line.strip()
        if not s or s.startswith("IP Virtual Server") or s.startswith("Prot "):
            continue
        if s.startswith("-> RemoteAddress:Port"):
            continue
        if s.startswith("TCP") or s.startswith("UDP"):
            parts = s.split()
            if len(parts) < 3:
                continue
            proto = parts[0].lower()
            host, port_text = parts[1].rsplit(":", 1)
            scheduler = parts[2].strip("[]")
            current = VirtualService(proto=proto, vip=host, port=int(port_text), scheduler=scheduler)
            state.services.append(current)
            continue
        if current is not None:
            parts = s.split()
            if len(parts) < 4:
                continue
            if ":" not in parts[1]:
                continue
            ip, ptxt = parts[1].rsplit(":", 1)
            forward = str(parts[2]).lower()
            method = "nat" if forward.startswith("masq") else "routing"
            current.reals.append(RealServer(ip=ip, port=int(ptxt), weight=int(parts[3]), method=method))
    return state


def parse_ipvsadm_stats(output: str) -> IpvsStatsSnapshot:
    """Parse `ipvsadm -ln --stats` output."""
    state = LiveIpvsState()
    current: VirtualService | None = None
    for line in output.splitlines():
        s = line.strip()
        if (
            not s
            or s.startswith("IP Virtual Server")
            or s.startswith("Prot ")
            or s.startswith("-> RemoteAddress:Port")
        ):
            continue
        parts = s.split()
        if s.startswith("TCP") or s.startswith("UDP"):
            if len(parts) < 7:
                continue
            proto = parts[0].lower()
            host, port_text = parts[1].rsplit(":", 1)
            current = VirtualService(
                proto=proto,
                vip=host,
                port=int(port_text),
                scheduler="unknown",
                conns=int(parts[2]),
                inpkts=int(parts[3]),
                outpkts=int(parts[4]),
                inbytes=int(parts[5]),
                outbytes=int(parts[6]),
            )
            state.services.append(current)
            continue
        if current is not None and s.startswith("->"):
            if len(parts) < 7 or ":" not in parts[1]:
                continue
            ip, ptxt = parts[1].rsplit(":", 1)
            current.reals.append(
                RealServer(
                    ip=ip,
                    port=int(ptxt),
                    weight=0,
                    method="unknown",
                    active_conn=int(parts[2]),
                    inpkts=int(parts[3]),
                    outpkts=int(parts[4]),
                    inbytes=int(parts[5]),
                    outbytes=int(parts[6]),
                )
            )
    return IpvsStatsSnapshot(services=state.services)


def read_live() -> LiveIpvsState:
    """Read live IPVS state."""
    proc = _run(["ipvsadm", "-ln"])
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "ipvsadm -ln failed")
    return parse_ipvsadm_ln(proc.stdout)


def read_stats() -> IpvsStatsSnapshot:
    """Read live IPVS stats."""
    proc = _run(["ipvsadm", "-ln", "--stats"])
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "ipvsadm --stats failed")
    return parse_ipvsadm_stats(proc.stdout)


def read_dump() -> str:
    """Read live IPVS dump in save format.

    Output:
    - Raw `ipvsadm -Sn` output text.
    """
    proc = _run(["ipvsadm", "-Sn"])
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "ipvsadm -Sn failed")
    return proc.stdout


def _svc_args(svc: VirtualService) -> list[str]:
    """Build service args for add/edit operations.

    Input:
    - svc: Virtual service model.

    Output:
    - ipvsadm args including scheduler.
    """
    return ["-t" if svc.proto == "tcp" else "-u", f"{svc.vip}:{svc.port}", "-s", svc.scheduler]


def _svc_selector_args(svc: VirtualService) -> list[str]:
    """Build service selector args for delete/real operations.

    Input:
    - svc: Virtual service model.

    Output:
    - ipvsadm selector args without scheduler.
    """
    return ["-t" if svc.proto == "tcp" else "-u", f"{svc.vip}:{svc.port}"]


def _run_checked(args: list[str]) -> None:
    """Run one ipvsadm command and fail on non-zero exit."""
    proc = _run(args)
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or f"command failed: {' '.join(args)}"
        raise RuntimeError(detail)


def _real_proxy_args(rs: RealServer) -> list[str]:
    """Build forwarding method args for real server operations.

    Input:
    - rs: Real server model.

    Output:
    - ipvsadm forwarding mode argument list.
    """
    method = str(rs.method).lower().strip()
    if method == "nat":
        return ["-m"]
    return ["-g"]


def apply_plan(plan: IpvsApplyPlan) -> IpvsApplyResult:
    """Apply IPVS plan."""
    try:
        for svc in plan.del_services:
            _run_checked(["ipvsadm", "-D"] + _svc_selector_args(svc))
        for svc in plan.add_services:
            _run_checked(["ipvsadm", "-A"] + _svc_args(svc))
        for svc, rs in plan.del_reals:
            _run_checked(["ipvsadm", "-d"] + _svc_selector_args(svc) + ["-r", f"{rs.ip}:{rs.port}"])
        for svc, rs in plan.add_reals:
            _run_checked(
                ["ipvsadm", "-a"] + _svc_selector_args(svc) + ["-r", f"{rs.ip}:{rs.port}"] + _real_proxy_args(rs) + ["-w", str(rs.weight)]
            )
        for svc, rs in plan.set_reals:
            _run_checked(
                ["ipvsadm", "-e"] + _svc_selector_args(svc) + ["-r", f"{rs.ip}:{rs.port}"] + _real_proxy_args(rs) + ["-w", str(rs.weight)]
            )
        return IpvsApplyResult(ok=True, message="ok")
    except Exception as exc:
        return IpvsApplyResult(ok=False, message=str(exc))


def clear_managed(managed: ManagedScope) -> IpvsApplyResult:
    """Remove managed services."""
    plan = IpvsApplyPlan(del_services=list(managed.services))
    return apply_plan(plan)


def replace_managed(plan: IpvsApplyPlan, managed: ManagedScope) -> IpvsApplyResult:
    """Clear managed services and apply plan."""
    result = clear_managed(managed)
    if not result.ok:
        return result
    return apply_plan(plan)
