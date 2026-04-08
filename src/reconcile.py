"""Desired/live reconciliation helpers."""

from __future__ import annotations

import socket
from dataclasses import asdict
from typing import Any

from src.ipvs_exec import IpvsApplyPlan, LiveIpvsState, RealServer, VirtualService


def _service_key_variants(proto: str, vip: str, port: int) -> set[tuple[str, str, int]]:
    """Build service match key variants for hostname/IP equivalence.

    Input:
    - proto: Service protocol.
    - vip: Service VIP as configured or read from live.
    - port: Service port.

    Output:
    - Set of key tuples that include raw and resolved VIP forms.
    """
    proto_norm = str(proto).lower()
    vip_raw = str(vip)
    keys: set[tuple[str, str, int]] = {(proto_norm, vip_raw, int(port))}
    try:
        for fam, _, _, _, sockaddr in socket.getaddrinfo(vip_raw, None):
            if fam in (socket.AF_INET, socket.AF_INET6) and sockaddr:
                keys.add((proto_norm, str(sockaddr[0]), int(port)))
    except Exception:
        pass
    return keys


def desired_services(snapshot: dict[str, Any]) -> list[VirtualService]:
    """Convert snapshot into VirtualService list."""
    out: list[VirtualService] = []
    for group in snapshot.get("groups", []):
        for svc in group.get("services", []):
            vs = VirtualService(
                proto=svc["proto"],
                vip=svc["vip"],
                port=int(svc["port"]),
                scheduler=svc["scheduler"],
                reals=[
                    RealServer(
                        ip=rs["ip"],
                        port=int(rs["port"]),
                        weight=int(rs["weight"]),
                        method=str(rs.get("method", rs.get("proxy_method", "routing"))),
                    )
                    for rs in svc.get("reals", [])
                ],
            )
            out.append(vs)
    return out


def build_apply_plan(desired: list[VirtualService], live: LiveIpvsState) -> IpvsApplyPlan:
    """Build a coarse apply plan.

    Input:
    - desired: list of desired virtual services.
    - live: current IPVS state read from ipvsadm.

    Output:
    - IpvsApplyPlan with add/set/del operations.
    """
    plan = IpvsApplyPlan()

    # Map all variant keys from live for hostname/IP equivalence
    live_by_variant: dict[tuple[str, str, int], VirtualService] = {}
    for s in live.services:
        for k in _service_key_variants(s.proto, s.vip, s.port):
            live_by_variant[k] = s

    # Map all variant keys from desired (used for del_services check)
    desired_by_variant: dict[tuple[str, str, int], VirtualService] = {}
    for s in desired:
        for k in _service_key_variants(s.proto, s.vip, s.port):
            desired_by_variant[k] = s

    for svc in desired:
        live_svc = next(
            (live_by_variant[k] for k in _service_key_variants(svc.proto, svc.vip, svc.port) if k in live_by_variant),
            None,
        )
        if live_svc is None:
            plan.add_services.append(svc)
            for rs in svc.reals:
                plan.add_reals.append((svc, rs))
            continue
        desired_rs = {(r.ip, r.port): r for r in svc.reals}
        live_rs = {(r.ip, r.port): r for r in live_svc.reals}
        for rs_key, rs in desired_rs.items():
            if rs_key not in live_rs:
                plan.add_reals.append((svc, rs))
            elif live_rs[rs_key].weight != rs.weight or str(live_rs[rs_key].method) != str(rs.method):
                plan.set_reals.append((svc, rs))
        for rs_key, rs in live_rs.items():
            if rs_key not in desired_rs:
                plan.del_reals.append((svc, rs))

    for svc in live.services:
        key = (svc.proto, svc.vip, svc.port)
        if key not in desired_by_variant:
            plan.del_services.append(svc)
    return plan


def build_report(snapshot: dict[str, Any], live: LiveIpvsState | None = None) -> dict[str, Any]:
    """Build desired-vs-live status report."""
    if live is None:
        live = LiveIpvsState()
    desired = desired_services(snapshot)
    live_keys: set[tuple[str, str, int]] = set()
    for s in live.services:
        live_keys.update(_service_key_variants(s.proto, s.vip, s.port))
    svc_meta: dict[tuple[str, str, int], tuple[str, str]] = {}
    for group in snapshot.get("groups", []):
        gname = str(group.get("group", "-"))
        for svc in group.get("services", []):
            key = (str(svc.get("proto")), str(svc.get("vip")), int(svc.get("port")))
            svc_meta[key] = (gname, str(svc.get("frontend_name", "-")))
    out_services: list[dict[str, Any]] = []
    for svc in desired:
        key = (svc.proto, svc.vip, svc.port)
        key_variants = _service_key_variants(svc.proto, svc.vip, svc.port)
        group_name, frontend_name = svc_meta.get(key, ("-", "-"))
        out_services.append(
            {
                "group": group_name,
                "frontend_name": frontend_name,
                "proto": svc.proto,
                "vip": svc.vip,
                "port": svc.port,
                "scheduler": svc.scheduler,
                "live_present": len(key_variants & live_keys) > 0,
                "backends": [asdict(rs) for rs in svc.reals],
            }
        )
    return {
        "services": out_services,
        "desired_generation": snapshot.get("desired_generation", 0),
        "config_version_mtime_seconds": snapshot.get("config_version_mtime", 0.0),
    }
