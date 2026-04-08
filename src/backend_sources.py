"""Backend source merging and normalization."""

from __future__ import annotations

import socket
from pathlib import Path

from src.models import Backend


def resolve_port(value: int | str, proto: str) -> int:
    """Resolve numeric/service-name port into integer."""
    if isinstance(value, int):
        return value
    if value.isdigit():
        return int(value)
    return int(socket.getservbyname(value, proto))


def merge_backends(
    inline_backends: list[Backend],
    file_backends: list[Backend],
    map_backends: list[Backend],
) -> list[Backend]:
    """Merge backend definitions with last-write-wins by IP."""
    merged: dict[str, Backend] = {}
    for item in inline_backends + file_backends + map_backends:
        merged[item.ip] = item
    return list(merged.values())


def resolve_backend_file_paths(config_dir: Path, backend_files: list[str]) -> list[Path]:
    """Resolve backend file list against config root."""
    out: list[Path] = []
    config_root = config_dir.resolve()
    for file_name in backend_files:
        p = Path(file_name)
        if p.is_absolute():
            raise ValueError(f"absolute backend file path is not allowed: {file_name}")
        resolved = (config_dir / p).resolve()
        if config_root not in resolved.parents and resolved != config_root:
            raise ValueError(f"backend file path escapes config dir: {file_name}")
        out.append(resolved)
    return out
