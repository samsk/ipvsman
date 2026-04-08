"""Version helpers."""

from __future__ import annotations

from datetime import datetime, timezone

__version__ = "0.1.0"
BUILD_TIME = datetime.now(timezone.utc).isoformat()


def get_version_string() -> str:
    """Return human-readable build version."""
    return f"{__version__} ({BUILD_TIME})"
