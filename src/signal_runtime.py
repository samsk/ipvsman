"""Signal handling."""

from __future__ import annotations

import signal
from types import FrameType
from typing import Callable


def install_handlers(on_reload: Callable[[], None], on_stop: Callable[[], None]) -> None:
    """Install SIGHUP and SIGTERM handlers."""

    def _hup(_signum: int, _frame: FrameType | None) -> None:
        on_reload()

    def _term(_signum: int, _frame: FrameType | None) -> None:
        on_stop()

    signal.signal(signal.SIGHUP, _hup)
    signal.signal(signal.SIGTERM, _term)
    signal.signal(signal.SIGINT, _term)
