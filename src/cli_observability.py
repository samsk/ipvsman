"""Detailed observability CLI output."""

from __future__ import annotations

import json
import time
from typing import Any

from src.reconcile import build_report


def _backend_state(weight: int) -> str:
    """Return backend state label.

    Input:
    - weight: Effective backend weight.

    Output:
    - "UP" when weight > 0, else "DOWN".
    """
    return "DOWN" if int(weight) == 0 else "UP"


def _service_state(backends: list[dict[str, Any]], live_present: bool) -> str:
    """Return service aggregate state.

    Input:
    - backends: Backend rows for one service.
    - live_present: Whether service exists in live IPVS.

    Output:
    - "UP" when service is live and all backends are UP, else "DEGRADED".
    """
    if not live_present:
        return "DEGRADED"
    for rs in backends:
        if _backend_state(int(rs["weight"])) == "DOWN":
            return "DEGRADED"
    return "UP"


def print_detailed(
    snapshot: dict[str, Any],
    live_state: Any,
    output: str,
    show_counters: bool,
    only_active: bool,
    filter_group: str | None = None,
    filter_frontend: str | None = None,
    filter_backend: str | None = None,
) -> None:
    """Print detailed status output."""
    report = build_report(snapshot, live_state)
    if filter_group:
        report["services"] = [svc for svc in report["services"] if svc.get("group") == filter_group]
    if filter_frontend:
        report["services"] = [svc for svc in report["services"] if f"{svc.get('group')}/{svc.get('frontend_name')}" == filter_frontend]
    if only_active:
        report["services"] = [svc for svc in report["services"] if svc["live_present"]]
    if filter_backend:
        for svc in report["services"]:
            svc["backends"] = [rs for rs in svc["backends"] if rs.get("ip") == filter_backend]
        report["services"] = [svc for svc in report["services"] if svc["backends"]]
    if output == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
        return
    svc_count = len(report["services"])
    print(f"status-detailed services={svc_count}")
    for svc in report["services"]:
        backends = svc["backends"]
        up_count = sum(1 for rs in backends if _backend_state(int(rs["weight"])) == "UP")
        svc_state = _service_state(backends, bool(svc["live_present"]))
        print(
            f"[{svc_state}] {svc['group']}/{svc['frontend_name']} "
            f"{svc['proto']} {svc['vip']}:{svc['port']} "
            f"sched={svc['scheduler']} live={svc['live_present']} "
            f"backends_up={up_count}/{len(backends)}"
        )
        for rs in svc["backends"]:
            state = _backend_state(int(rs["weight"]))
            line = (
                f"  - [{state}] {rs['ip']}:{rs['port']} "
                f"weight={rs['weight']} conn_active={rs.get('active_conn', 0)} "
                f"conn_inactive={rs.get('inactive_conn', 0)}"
            )
            if show_counters:
                line += f" pkts_in={rs.get('inpkts', 0)} pkts_out={rs.get('outpkts', 0)}"
            print(line)


def maybe_watch(interval: float | None) -> None:
    """Sleep in watch mode."""
    if interval is None:
        return
    time.sleep(max(0.1, interval))
