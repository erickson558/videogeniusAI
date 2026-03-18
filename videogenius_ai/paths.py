from __future__ import annotations

import sys
from pathlib import Path


def get_app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


APP_ROOT = get_app_root()
CONFIG_PATH = APP_ROOT / "config.json"
LOG_PATH = APP_ROOT / "log.txt"
HISTORY_DIR = APP_ROOT / "history"
OUTPUT_DIR = APP_ROOT / "output"
TEMP_DIR = APP_ROOT / "temp"
RUNTIME_DIR = APP_ROOT / "runtime"
WORKFLOWS_DIR = APP_ROOT / "workflows"
