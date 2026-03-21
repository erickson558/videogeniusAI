from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Scene:
    scene_number: int
    scene_title: str = ""
    description: str = ""
    visual_description: str = ""
    visual_prompt: str = ""
    narration: str = ""
    duration_seconds: int = 8
    transition: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GenerationRequest:
    topic: str
    visual_style: str
    audience: str
    narrative_tone: str
    video_format: str
    output_language: str
    total_duration_seconds: int
    scene_count: int
    generation_mode: str
    model: str
    temperature: float
    max_tokens: int


@dataclass
class VideoProject:
    title: str
    summary: str
    general_script: str
    structure: str
    estimated_total_duration_seconds: int
    output_language: str
    generation_mode: str
    source_topic: str
    visual_style: str
    audience: str
    narrative_tone: str
    video_format: str
    scenes: list[Scene] = field(default_factory=list)
    raw_response: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["scenes"] = [scene.to_dict() for scene in self.scenes]
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "VideoProject":
        scenes_payload = payload.get("scenes", [])
        scenes = [Scene(**scene) for scene in scenes_payload if isinstance(scene, dict)]
        body = {key: value for key, value in payload.items() if key != "scenes"}
        body["scenes"] = scenes
        return cls(**body)


@dataclass
class VideoRenderRequest:
    project: VideoProject
    output_dir: str
    provider: str = "Storyboard local"
    aspect_ratio: str = "9:16"
    request_timeout_seconds: int = 180
    render_captions: bool = True
    comfyui_base_url: str = "http://127.0.0.1:8188"
    comfyui_worker_urls: str = ""
    parallel_scene_workers: int = 1
    render_gpu_preference: str = "Auto"
    comfyui_checkpoint: str = ""
    comfyui_workflow_path: str = ""
    comfyui_negative_prompt: str = ""
    comfyui_poll_interval_seconds: int = 2
    tts_backend: str = "Windows local"
    ffmpeg_path: str = ""
    piper_executable_path: str = ""
    piper_model_path: str = ""
    avatar_source_image_path: str = ""


@dataclass
class RenderedVideoResult:
    provider: str
    file_path: Path | None = None
    remote_video_id: str = ""
    remote_video_url: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class GeneratedSceneAsset:
    asset_type: str
    file_path: Path
    source_payload: dict[str, Any] = field(default_factory=dict)
