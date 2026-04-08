"""Shared listing builders for CLI and API."""

from __future__ import annotations

from typing import Any


def list_services(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Return all service rows from snapshot.

    Input:
    - snapshot: desired snapshot mapping.

    Output:
    - Flat list of service dictionaries.
    """
    return [svc for grp in snapshot.get("groups", []) for svc in grp.get("services", [])]


def list_frontends(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Return flattened frontend rows.

    Input:
    - snapshot: desired snapshot mapping.

    Output:
    - List of frontend rows.
    """
    return [
        {
            "group": svc["group"],
            "name": svc["frontend_name"],
            "proto": svc["proto"],
            "vip": svc["vip"],
            "port": svc["port"],
        }
        for svc in list_services(snapshot)
    ]


def list_backends(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Return flattened backend rows.

    Input:
    - snapshot: desired snapshot mapping.

    Output:
    - List of backend rows.
    """
    rows: list[dict[str, Any]] = []
    for svc in list_services(snapshot):
        for rs in svc.get("reals", []):
            rows.append(
                {
                    "group": svc["group"],
                    "frontend": svc["frontend_name"],
                    "proto": svc["proto"],
                    "vip": svc["vip"],
                    "frontend_port": svc["port"],
                    **rs,
                }
            )
    return rows


def list_healthchecks(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Return flattened healthcheck rows.

    Input:
    - snapshot: desired snapshot mapping.

    Output:
    - List of healthcheck rows.
    """
    return [
        {"group": svc["group"], "frontend": svc["frontend_name"], "healthcheck": svc["healthcheck"]}
        for svc in list_services(snapshot)
    ]
