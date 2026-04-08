"""Manual check trigger helpers."""

from __future__ import annotations

from typing import Any, Callable

from src.check_runtime import run_one_check
from src.models import CheckTarget, HealthCheck


def run_manual_checks(
    snapshot: dict[str, Any],
    group: str | None,
    backend_ip: str | None,
    on_result: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Run one-shot checks and return summary.

    Input:
    - snapshot: Desired snapshot with services/reals/check targets.
    - group: Optional group filter.
    - backend_ip: Optional backend IP filter.
    - on_result: Optional callback invoked for each check result.

    Output:
    - Summary dict with total/ok/failed and per-check results.
    """
    # Explicit targeting (group/backend) can run checks even when healthcheck.disable is set.
    explicit_target = (group is not None) or (backend_ip is not None)
    results: list[dict[str, Any]] = []
    for grp in snapshot.get("groups", []):
        for service in grp.get("services", []):
            if group and service["group"] != group:
                continue
            if service.get("disabled"):
                continue
            hc = HealthCheck.model_validate(service["healthcheck"])
            if hc.disable and not explicit_target:
                continue
            for backend in service.get("reals", []):
                if backend_ip and backend["ip"] != backend_ip:
                    continue
                target = CheckTarget.model_validate(backend["check_target"])
                ok, message = run_one_check(target, hc)
                results.append(
                    {
                        "group": service["group"],
                        "frontend": service["frontend_name"],
                        "backend_ip": backend["ip"],
                        "ok": ok,
                        "message": message,
                    }
                )
                if on_result is not None:
                    on_result(results[-1])
    ok_count = sum(1 for row in results if row["ok"])
    return {"total": len(results), "ok": ok_count, "failed": len(results) - ok_count, "results": results}
