from __future__ import annotations

import logging
import os
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .paths import LOG_PATH
from .version import DISPLAY_VERSION


DEFAULT_LOGGER_NAME = "videogenius_ai"
LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(threadName)s | %(filename)s:%(lineno)d | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def _normalize_logger_name(name: str | None, root_name: str) -> str:
    text = (name or "").strip()
    if not text or text == root_name or text == "__main__":
        return root_name
    if text.startswith(f"{root_name}."):
        return text
    return f"{root_name}.{text}"


def _reset_handlers(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()


def _install_exception_hooks(logger: logging.Logger) -> None:
    if getattr(logger, "_videogenius_exception_hooks_installed", False):
        return

    original_sys_excepthook = sys.excepthook

    def handle_sys_exception(exc_type: type[BaseException], exc_value: BaseException, exc_traceback: object) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            original_sys_excepthook(exc_type, exc_value, exc_traceback)
            return
        logger.critical("Unhandled process exception", exc_info=(exc_type, exc_value, exc_traceback))

    sys.excepthook = handle_sys_exception

    if hasattr(threading, "excepthook"):
        original_thread_excepthook = threading.excepthook

        def handle_thread_exception(args: threading.ExceptHookArgs) -> None:
            if issubclass(args.exc_type, KeyboardInterrupt):
                original_thread_excepthook(args)
                return
            thread_name = args.thread.name if args.thread else "unknown"
            logger.critical(
                "Unhandled thread exception | thread=%s",
                thread_name,
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            )

        threading.excepthook = handle_thread_exception

    setattr(logger, "_videogenius_exception_hooks_installed", True)


def configure_logging(
    name: str | None = None,
    *,
    log_path: Path | None = None,
    level: int = logging.INFO,
    root_name: str = DEFAULT_LOGGER_NAME,
    reset: bool = False,
    install_exception_hooks: bool = True,
) -> logging.Logger:
    logger = logging.getLogger(root_name)
    resolved_log_path = Path(log_path or LOG_PATH).resolve()
    resolved_log_path.parent.mkdir(parents=True, exist_ok=True)
    configured_path = getattr(logger, "_videogenius_log_path", None)

    if reset or configured_path != str(resolved_log_path):
        _reset_handlers(logger)

    logger.setLevel(level)
    if not logger.handlers:
        handler = RotatingFileHandler(
            resolved_log_path,
            maxBytes=2_000_000,
            backupCount=5,
            encoding="utf-8",
            delay=True,
        )
        handler.setLevel(level)
        handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))
        logger.addHandler(handler)
        logger.info(
            "Logging initialized | version=%s | pid=%s | path=%s",
            DISPLAY_VERSION,
            os.getpid(),
            resolved_log_path,
        )

    logger.propagate = False
    setattr(logger, "_videogenius_log_path", str(resolved_log_path))

    if install_exception_hooks:
        _install_exception_hooks(logger)

    return logging.getLogger(_normalize_logger_name(name, root_name))
