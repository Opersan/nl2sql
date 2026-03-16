"""Centralised logging setup."""

from __future__ import annotations

import logging
import sys


def get_logger(name: str, *, level: int = logging.INFO) -> logging.Logger:
    """Return a configured logger with a consistent format.

    Creates a StreamHandler to *stderr* so that structured output on stdout
    is not polluted.
    """
    logger = logging.getLogger(name)

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        formatter = logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    logger.setLevel(level)
    return logger
