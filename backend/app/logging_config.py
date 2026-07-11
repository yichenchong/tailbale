"""Application logging configuration."""

from __future__ import annotations

import logging
import os
import sys

LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S%z"


def configure_logging() -> None:
    """Configure root logging: timestamped format and an explicit level.

    Without an explicit level the root logger stays at Python's default of
    WARNING, which silently drops every ``logger.info(...)`` the app emits
    (migrations, reconcile sweeps, cert issuance, edge builds, lego output,
    etc.). The level is driven by ``LOG_LEVEL`` (default ``INFO``).
    """
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    if not isinstance(level, int):
        level = logging.INFO

    formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)

    root = logging.getLogger()
    root.setLevel(level)
    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(formatter)
        root.addHandler(handler)
    else:
        for handler in root.handlers:
            handler.setFormatter(formatter)

    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi"):
        logger = logging.getLogger(logger_name)
        if logger.handlers:
            logger.propagate = False
            for handler in logger.handlers:
                handler.setFormatter(formatter)
