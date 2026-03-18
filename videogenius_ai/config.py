from __future__ import annotations

import json
import re
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .paths import CONFIG_PATH, HISTORY_DIR, OUTPUT_DIR, RUNTIME_DIR, TEMP_DIR, WORKFLOWS_DIR
from .version import DISPLAY_VERSION

DEFAULT_WINDOW_GEOMETRY = "1460x900+80+40"
MIN_WINDOW_WIDTH = 1320
MIN_WINDOW_HEIGHT = 840


def sanitize_window_geometry(value: str, fallback: str = DEFAULT_WINDOW_GEOMETRY) -> str:
    text = (value or "").strip()
    match = re.fullmatch(r"(\d+)x(\d+)([+-]\d+)([+-]\d+)", text)
    if not match:
        return fallback

    width = int(match.group(1))
    height = int(match.group(2))
    x_pos = int(match.group(3))
    y_pos = int(match.group(4))
    if width < MIN_WINDOW_WIDTH or height < MIN_WINDOW_HEIGHT:
        return fallback
    if abs(x_pos) > 10000 or abs(y_pos) > 10000:
        return fallback
    return f"{width}x{height}{x_pos:+d}{y_pos:+d}"


@dataclass
class AppConfig:
    app_version: str = DISPLAY_VERSION
    appearance_mode: str = "dark"
    lmstudio_base_url: str = "http://127.0.0.1:1234"
    model: str = ""
    api_key: str = ""
    video_provider: str = "Storyboard local"
    video_aspect_ratio: str = "9:16"
    render_captions: bool = True
    comfyui_base_url: str = "http://127.0.0.1:8188"
    comfyui_worker_urls: str = ""
    parallel_scene_workers: int = 1
    comfyui_checkpoint: str = ""
    comfyui_workflow_path: str = ""
    comfyui_negative_prompt: str = ""
    comfyui_poll_interval_seconds: int = 2
    tts_backend: str = "Windows local"
    ffmpeg_path: str = ""
    piper_executable_path: str = ""
    piper_model_path: str = ""
    setup_completed: bool = False
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
    window_geometry: str = DEFAULT_WINDOW_GEOMETRY
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
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        WORKFLOWS_DIR.mkdir(parents=True, exist_ok=True)
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
        payload["window_geometry"] = sanitize_window_geometry(str(payload.get("window_geometry", DEFAULT_WINDOW_GEOMETRY)))
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
