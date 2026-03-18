from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .paths import CONFIG_PATH, HISTORY_DIR, OUTPUT_DIR, TEMP_DIR
from .version import DISPLAY_VERSION


@dataclass
class AppConfig:
    app_version: str = DISPLAY_VERSION
    appearance_mode: str = "dark"
    lmstudio_base_url: str = "http://127.0.0.1:1234"
    model: str = ""
    api_key: str = ""
    temperature: float = 0.7
    scene_count: int = 6
    output_language: str = "Espanol"
    estimated_duration_seconds: int = 60
    video_topic: str = ""
    visual_style: str = "Cyberpunk cinematografico"
    audience: str = "General"
    narrative_tone: str = "Cinematico e inmersivo"
    video_format: str = "YouTube Short"
    generation_mode: str = "Proyecto completo"
    output_dir: str = "output"
    auto_start_enabled: bool = False
    auto_close_enabled: bool = False
    auto_close_seconds: int = 60
    window_geometry: str = "1460x900+80+40"
    window_zoomed: bool = False
    history_limit: int = 100
    json_retry_attempts: int = 3
    request_timeout_seconds: int = 120
    max_tokens: int = 2800


class ConfigManager:
    def __init__(self, config_path: Path | None = None) -> None:
        self.config_path = config_path or CONFIG_PATH
        self._lock = threading.RLock()
        self.config = self._load()
        self.ensure_runtime_directories()

    def ensure_runtime_directories(self) -> None:
        HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        TEMP_DIR.mkdir(parents=True, exist_ok=True)
        Path(self.resolve_output_dir()).mkdir(parents=True, exist_ok=True)

    def _load(self) -> AppConfig:
        if not self.config_path.exists():
            config = AppConfig()
            self._write(config)
            return config

        try:
            with self.config_path.open("r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except (json.JSONDecodeError, OSError):
            config = AppConfig()
            self._write(config)
            return config

        payload = asdict(AppConfig())
        payload.update(raw if isinstance(raw, dict) else {})
        payload["app_version"] = DISPLAY_VERSION
        return AppConfig(**payload)

    def _write(self, config: AppConfig | None = None) -> None:
        data = asdict(config or self.config)
        data["app_version"] = DISPLAY_VERSION
        temp_path = self.config_path.with_suffix(".tmp")
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
        temp_path.replace(self.config_path)

    def save(self) -> None:
        with self._lock:
            self._write()
            self.ensure_runtime_directories()

    def update(self, **kwargs: Any) -> None:
        with self._lock:
            for key, value in kwargs.items():
                if hasattr(self.config, key):
                    setattr(self.config, key, value)
            self.config.app_version = DISPLAY_VERSION
            self._write()
            self.ensure_runtime_directories()

    def resolve_output_dir(self) -> str:
        configured = Path(self.config.output_dir)
        if not configured.is_absolute():
            configured = self.config_path.parent / configured
        return str(configured.resolve())
