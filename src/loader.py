"""YAML config loader and validator."""

from __future__ import annotations

import copy
import json
import os
import socket
from pathlib import Path
from typing import Any

import yaml
try:
    from pydantic import ValidationError
except ModuleNotFoundError:  # pragma: no cover
    ValidationError = ValueError

from src.backend_sources import merge_backends, resolve_backend_file_paths, resolve_port
from src.constants import MAX_CONFIG_FILE_BYTES, MAX_CONFIG_FILES
from src.models import Backend, CheckTarget, Frontend, Group, HealthCheck


def _load_yaml_file(path: Path) -> Any:
    """Read and safe-load one YAML file."""
    if path.stat().st_size > MAX_CONFIG_FILE_BYTES:
        raise ValueError(f"{path}: too large")
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _glob_files(root: Path, pattern: str) -> list[Path]:
    files = sorted(root.glob(pattern))
    if len(files) > MAX_CONFIG_FILES:
        raise ValueError(f"too many config files under {root}/{pattern}")
    return files


def _ensure_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    return [value]


def _load_backend_pool(config_dir: Path, group: Group) -> list[Backend]:
    inline = [Backend.model_validate(x) for x in group.backends]
    file_backends: list[Backend] = []
    for file_path in resolve_backend_file_paths(config_dir, group.backend_files):
        content = _load_yaml_file(file_path)
        for row in _ensure_list(content):
            file_backends.append(Backend.model_validate(row))

    map_backends: list[Backend] = []
    if group.backend_map_ref:
        map_files = _glob_files(config_dir / "backend-maps", "*.yaml")
        merged_maps: dict[str, list[dict[str, Any]]] = {}
        for mf in map_files:
            content = _load_yaml_file(mf)
            if not isinstance(content, dict):
                raise ValueError(f"{mf}: map file must be mapping")
            for key, value in content.items():
                if key in merged_maps:
                    raise ValueError(f"duplicate backend map key: {key}")
                merged_maps[key] = _ensure_list(value)
        for row in merged_maps.get(group.backend_map_ref, []):
            map_backends.append(Backend.model_validate(row))

    return merge_backends(inline, file_backends, map_backends)


def _load_check_refs(config_dir: Path) -> dict[str, CheckTarget]:
    check_files = _glob_files(config_dir / "check-refs", "*.yaml")
    refs: dict[str, CheckTarget] = {}
    for file_path in check_files:
        content = _load_yaml_file(file_path)
        if not isinstance(content, dict):
            raise ValueError(f"{file_path}: check-refs file must be mapping")
        for key, value in content.items():
            if key in refs:
                raise ValueError(f"duplicate check_ref: {key}")
            refs[key] = CheckTarget.model_validate(value)
    return refs


def _resolve_port_map(backend: Backend, frontend_proto: str, frontend_port: int) -> int:
    raw_map = backend.port_map
    if isinstance(raw_map, list):
        port_map: dict[str, int] = {}
        for item in raw_map:
            if isinstance(item, dict):
                for key, port in item.items():
                    port_map[str(key)] = int(port)
    else:
        port_map = dict(raw_map)
    proto_key = f"{frontend_port}/{frontend_proto.lower()}"
    if proto_key in port_map:
        return int(port_map[proto_key])
    if str(frontend_port) in port_map:
        return int(port_map[str(frontend_port)])
    if "*" in port_map:
        return int(port_map["*"])
    return frontend_port


def _expand_backend_hosts(
    backend_pool: list[Backend],
    warnings: list[str],
    group_name: str,
) -> list[Backend]:
    """Resolve backend hostnames into IP backends.

    Input:
    - backend_pool: Backend list loaded from config sources.
    - warnings: Mutable warning collector.
    - group_name: Group name for warning context.

    Output:
    - Backend list with hostnames expanded to IP entries.
    """
    expanded: list[Backend] = []
    for backend in backend_pool:
        ip_raw = str(backend.ip)
        try:
            infos = socket.getaddrinfo(ip_raw, None, proto=socket.IPPROTO_TCP)
            resolved = sorted({str(info[4][0]) for info in infos if info and info[4] and info[4][0]})
        except Exception:
            resolved = []
        if not resolved:
            expanded.append(backend)
            continue
        if len(resolved) > 1:
            warnings.append(f"group={group_name} backend={ip_raw} resolved multiple IPs: {', '.join(resolved)}")
        for ip_addr in resolved:
            be = copy.deepcopy(backend)
            be.ip = ip_addr
            expanded.append(be)
    return expanded


def load_snapshot(config_dir: Path) -> dict[str, Any]:
    """Load full desired snapshot from config directory."""
    group_files = _glob_files(config_dir / "groups", "*.yaml")
    check_refs = _load_check_refs(config_dir)
    groups: list[dict[str, Any]] = []
    warnings: list[str] = []
    seen_group: set[str] = set()
    loaded_files: set[Path] = set(group_files)
    loaded_files.update(_glob_files(config_dir / "backends", "*.yaml"))
    loaded_files.update(_glob_files(config_dir / "backend-maps", "*.yaml"))
    loaded_files.update(_glob_files(config_dir / "check-refs", "*.yaml"))

    for gfile in group_files:
        content = _load_yaml_file(gfile)
        for row in _ensure_list(content):
            try:
                group = Group.model_validate(row)
            except ValidationError as exc:
                raise ValueError(f"{gfile}: {exc}") from exc
            group_hc = HealthCheck.model_validate(group.healthcheck)
            group_frontends = [Frontend.model_validate(x) for x in group.frontends]
            if group.group in seen_group:
                raise ValueError(f"duplicate group: {group.group}")
            seen_group.add(group.group)
            backend_pool = _expand_backend_hosts(_load_backend_pool(config_dir, group), warnings, group.group)
            services: list[dict[str, Any]] = []
            group_vips = _ensure_list(group.vip) if group.vip is not None else []

            for frontend in group_frontends:
                fport = resolve_port(frontend.port, frontend.proto)
                vips = _ensure_list(frontend.vip) if frontend.vip is not None else group_vips
                scheduler = frontend.scheduler or group.scheduler or "wrr"
                for vip in vips:
                    if vip is None:
                        raise ValueError(f"group={group.group} frontend={frontend.name}: vip missing")
                    reals: list[dict[str, Any]] = []
                    for backend in backend_pool:
                        if backend.disabled or group.disabled or frontend.disabled:
                            eff_weight = 0
                        else:
                            eff_weight = backend.weight
                        rs_port = _resolve_port_map(backend, frontend.proto, fport)
                        target = backend.check_target
                        if target is None and backend.check_ref:
                            target = check_refs.get(backend.check_ref)
                        if target is None:
                            target = CheckTarget(ip=backend.ip, port=rs_port, type=group_hc.type)
                        method = backend.method if getattr(backend, "method", None) else (backend.proxy_method or "routing")
                        reals.append(
                            {
                                "ip": backend.ip,
                                "port": rs_port,
                                "configured_weight": backend.weight,
                                "weight": eff_weight,
                                "disabled": backend.disabled,
                                "method": method,
                                "check_target": target.model_dump(),
                            }
                        )
                    services.append(
                        {
                            "group": group.group,
                            "frontend_name": frontend.name,
                            "proto": frontend.proto,
                            "vip": str(vip),
                            "port": fport,
                            "scheduler": scheduler,
                            "disabled": bool(group.disabled or frontend.disabled),
                            "healthcheck": group_hc.model_dump(),
                            "reals": reals,
                        }
                    )
            groups.append({"group": group.group, "services": services, "disabled": group.disabled})

    seen_vs: set[tuple[str, str, int]] = set()
    for grp in groups:
        for svc in grp.get("services", []):
            key = (str(svc["proto"]), str(svc["vip"]), int(svc["port"]))
            if key in seen_vs:
                raise ValueError(
                    "duplicate virtual service "
                    f"(proto={key[0]} vip={key[1]} port={key[2]} "
                    f"group={svc.get('group')} frontend={svc.get('frontend_name')})"
                )
            seen_vs.add(key)

    newest_mtime = 0.0
    for file_path in loaded_files:
        newest_mtime = max(newest_mtime, file_path.stat().st_mtime)

    return {
        "groups": groups,
        "warnings": warnings,
        "loaded_files_count": len(loaded_files),
        "config_version_mtime": newest_mtime,
        "raw": json.dumps(groups, sort_keys=True),
    }
