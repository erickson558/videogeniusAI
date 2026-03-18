from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from .paths import LOG_PATH


def configure_logging() -> logging.Logger:
    logger = logging.getLogger("videogenius_ai")
    if logger.handlers:
        return logger

    LOG_PATH.touch(exist_ok=True)
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.propagate = False
    return logger

