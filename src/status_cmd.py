"""Status command helpers."""

from __future__ import annotations

import json
from typing import Any

from src.reconcile import build_report


def _backend_state(weight: int) -> str:
    """Return backend state from configured weight.

    Input:
    - weight: Effective backend weight.

    Output:
    - "UP" when weight > 0, else "DOWN".
    """
    return "DOWN" if int(weight) == 0 else "UP"


def _service_state(backends: list[dict[str, Any]], live_present: bool) -> str:
    """Return aggregate service state.

    Input:
    - backends: Service backend rows.
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


def print_status(snapshot: dict[str, Any], live_state: Any, output: str = "table") -> int:
    """Print status and return exit code."""
    report = build_report(snapshot, live_state)
    degraded = False
    for svc in report["services"]:
        for rs in svc["backends"]:
            if int(rs["weight"]) == 0:
                degraded = True
    if output == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        status_label = "DEGRADED" if degraded else "OK"
        print(f"status={status_label} services={len(report['services'])}")
        for svc in report["services"]:
            backends = svc["backends"]
            up_count = sum(1 for rs in backends if _backend_state(int(rs["weight"])) == "UP")
            svc_state = _service_state(backends, bool(svc["live_present"]))
            print(
                f"[{svc_state}] {svc['proto']} {svc['vip']}:{svc['port']} "
                f"group={svc['group']} frontend={svc['frontend_name']} "
                f"sched={svc['scheduler']} live={svc['live_present']} "
                f"backends_up={up_count}/{len(backends)}"
            )
            for rs in backends:
                state = _backend_state(int(rs["weight"]))
                print(f"  - [{state}] {rs['ip']}:{rs['port']} weight={rs['weight']}")
    return 1 if degraded else 0
