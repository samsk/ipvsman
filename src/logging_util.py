"""Logging setup."""

from __future__ import annotations

import logging
import logging.handlers

from src.constants import SYSLOG_IDENT


def setup_logging(level: str, no_syslog: bool, debug: bool = False) -> logging.Logger:
    """Configure and return logger."""
    logger = logging.getLogger(SYSLOG_IDENT)
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG if debug else getattr(logging, level.upper(), logging.INFO))

    stream = logging.StreamHandler()
    stream.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(stream)

    if not no_syslog:
        try:
            syslog = logging.handlers.SysLogHandler(address="/dev/log")
            syslog.setFormatter(logging.Formatter(f"{SYSLOG_IDENT}[%(process)d]: %(levelname)s %(message)s"))
            logger.addHandler(syslog)
        except OSError:
            logger.warning("syslog not available, continuing with stderr only")

    return logger
