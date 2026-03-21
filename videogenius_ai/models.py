from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .utils import safe_float, safe_int


@dataclass
class SceneShot:
    shot_number: int = 1
    duration_seconds: float = 2.0
    shot_type: str = ""
    camera_angle: str = ""
    camera_motion: str = ""
    focal_subject: str = ""
    action: str = ""
    environment: str = ""
    lighting: str = ""
    mood: str = ""
    color_palette: str = ""
    visual_prompt: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any], index: int = 0) -> "SceneShot":
        return cls(
            shot_number=safe_int(payload.get("shot_number") or payload.get("number"), index + 1),
            duration_seconds=max(0.4, safe_float(payload.get("duration_seconds") or payload.get("duration"), 2.0)),
            shot_type=str(payload.get("shot_type") or payload.get("framing") or ""),
            camera_angle=str(payload.get("camera_angle") or payload.get("angle") or ""),
            camera_motion=str(payload.get("camera_motion") or payload.get("motion") or ""),
            focal_subject=str(payload.get("focal_subject") or payload.get("subject") or ""),
            action=str(payload.get("action") or payload.get("movement") or ""),
            environment=str(payload.get("environment") or payload.get("setting") or ""),
            lighting=str(payload.get("lighting") or payload.get("lighting_style") or ""),
            mood=str(payload.get("mood") or payload.get("emotion") or ""),
            color_palette=str(payload.get("color_palette") or payload.get("palette") or ""),
            visual_prompt=str(payload.get("visual_prompt") or payload.get("prompt") or ""),
        )


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
    cinematic_intent: str = ""
    camera_language: str = ""
    lighting_style: str = ""
    color_palette: str = ""
    energy_level: str = ""
    negative_prompt: str = ""
    shots: list[SceneShot] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any], index: int = 0) -> "Scene":
        shots_payload = payload.get("shots", [])
        shots = [
            SceneShot.from_dict(shot, shot_index)
            for shot_index, shot in enumerate(shots_payload)
            if isinstance(shot, dict)
        ]
        return cls(
            scene_number=safe_int(payload.get("scene_number") or payload.get("number") or payload.get("scene"), index + 1),
            scene_title=str(payload.get("scene_title") or payload.get("title") or f"Scene {index + 1}"),
            description=str(payload.get("description") or payload.get("scene_description") or ""),
            visual_description=str(payload.get("visual_description") or payload.get("shot_description") or ""),
            visual_prompt=str(payload.get("visual_prompt") or payload.get("prompt") or ""),
            narration=str(payload.get("narration") or payload.get("voiceover") or ""),
            duration_seconds=max(1, safe_int(payload.get("duration_seconds") or payload.get("duration"), 8)),
            transition=str(payload.get("transition") or payload.get("transition_to_next") or ""),
            cinematic_intent=str(payload.get("cinematic_intent") or payload.get("intent") or ""),
            camera_language=str(payload.get("camera_language") or payload.get("camera_style") or ""),
            lighting_style=str(payload.get("lighting_style") or payload.get("lighting") or ""),
            color_palette=str(payload.get("color_palette") or payload.get("palette") or ""),
            energy_level=str(payload.get("energy_level") or payload.get("energy") or ""),
            negative_prompt=str(payload.get("negative_prompt") or ""),
            shots=shots,
        )


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
        scenes = [Scene.from_dict(scene, index) for index, scene in enumerate(scenes_payload) if isinstance(scene, dict)]
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
