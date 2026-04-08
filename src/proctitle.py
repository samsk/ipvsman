"""Set process title when available."""

from __future__ import annotations


def build_process_title(argv: list[str], base: str = "ipvsman.py", max_len: int = 255) -> str:
    """Build process title including CLI args.

    Input:
    - argv: Argument vector without executable name.
    - base: Base process name.
    - max_len: Maximum title length.

    Output:
    - Process title suitable for setproctitle.
    """
    if not argv:
        return base
    title = f"{base} {' '.join(argv)}"
    return title[:max_len]


def apply_proctitle(name: str) -> None:
    """Try setting process title, ignore failures."""
    try:
        import setproctitle  # type: ignore[import-not-found]

        setproctitle.setproctitle(name)
    except Exception:
        return
