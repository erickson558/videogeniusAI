from __future__ import annotations

from dataclasses import asdict, dataclass, field
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
