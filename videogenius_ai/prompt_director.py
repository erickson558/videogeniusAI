from __future__ import annotations

import re
from typing import Iterable

from .models import Scene, SceneShot, VideoProject

BASE_NEGATIVE_TOKENS = [
    "low quality",
    "blurry",
    "muddy details",
    "washed out colors",
    "flat lighting",
    "banding",
    "compression artifacts",
    "watermark",
    "logo",
    "text overlay",
    "subtitle box",
    "duplicated subjects",
    "bad anatomy",
    "extra limbs",
    "deformed hands",
    "disfigured face",
    "cropped head",
    "out of frame subject",
]

THEME_KEYWORDS = {
    "electric": ["electric arcs", "neon energy", "charged atmosphere", "glowing plasma trails"],
    "neon": ["neon reflections", "luminous haze", "wet reflective surfaces"],
    "cyber": ["futuristic city scale", "holographic glow", "dense layered depth"],
    "storm": ["storm clouds", "lightning veins", "dramatic weather"],
    "ocean": ["ocean spray", "misty horizon", "cinematic coastline"],
    "sea": ["rolling waves", "salt haze", "wind-blown water textures"],
    "mountain": ["epic mountain vista", "atmospheric depth", "layered ridgelines"],
    "forest": ["god rays through trees", "lush vegetation", "volumetric mist"],
    "jungle": ["dense jungle canopy", "humid air", "wild overgrowth"],
    "desert": ["heat shimmer", "towering dunes", "golden dust trails"],
    "space": ["cosmic scale", "nebula glow", "stellar particles"],
    "galaxy": ["galactic clouds", "celestial light", "astral depth"],
    "lava": ["molten light", "embers in the air", "volcanic glow"],
    "fire": ["ember particles", "radiant heat", "flickering orange highlights"],
    "snow": ["icy atmosphere", "powder snow", "crisp blue shadows"],
}

CAMERA_HINTS = {
    "9:16": "vertical 9:16 cinematic composition for premium short-form video",
    "16:9": "widescreen cinematic composition with strong environmental scale",
    "1:1": "square cinematic composition with strong central framing",
}


def _compact_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def _split_csvish(value: str) -> list[str]:
    normalized = _compact_text(value).replace("|", ",").replace(";", ",")
    return [item.strip() for item in normalized.split(",") if item.strip()]


def _dedupe_parts(parts: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for raw in parts:
        value = _compact_text(raw)
        if not value:
            continue
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(value)
    return ordered


def _theme_tokens(*texts: str) -> list[str]:
    haystack = " ".join(_compact_text(text).casefold() for text in texts if text)
    matches: list[str] = []
    for keyword, motifs in THEME_KEYWORDS.items():
        if keyword in haystack:
            matches.extend(motifs)
    return _dedupe_parts(matches)[:6]


def summarize_scene_shots(scene: Scene, *, max_shots: int = 4) -> str:
    fragments: list[str] = []
    for shot in scene.shots[:max_shots]:
        shot_parts = _dedupe_parts(
            [
                shot.shot_type,
                shot.camera_angle,
                shot.camera_motion,
                shot.focal_subject,
                shot.action,
                shot.environment,
                shot.lighting,
                shot.mood,
            ]
        )
        if shot_parts:
            fragments.append(", ".join(shot_parts))
    return " | ".join(fragments)


def build_scene_negative_prompt(*parts: str) -> str:
    tokens = _dedupe_parts([*BASE_NEGATIVE_TOKENS, *parts])
    return ", ".join(tokens)


def build_cinematic_scene_prompt(
    project: VideoProject,
    scene: Scene,
    *,
    aspect_ratio: str = "9:16",
    shot: SceneShot | None = None,
    include_output_guardrails: bool = True,
) -> str:
    theme_tokens = _theme_tokens(
        project.source_topic,
        project.visual_style,
        project.narrative_tone,
        scene.scene_title,
        scene.description,
        scene.visual_description,
        scene.visual_prompt,
        shot.visual_prompt if shot else "",
        shot.environment if shot else "",
        shot.action if shot else "",
    )
    shot_summary = summarize_scene_shots(scene)
    detail_parts = _dedupe_parts(
        [
            project.visual_style,
            project.video_format,
            project.narrative_tone,
            CAMERA_HINTS.get(aspect_ratio, CAMERA_HINTS["9:16"]),
            scene.scene_title,
            scene.cinematic_intent,
            scene.description,
            scene.visual_description,
            scene.visual_prompt,
            scene.camera_language,
            scene.lighting_style,
            scene.color_palette,
            scene.energy_level,
            shot.shot_type if shot else "",
            shot.camera_angle if shot else "",
            shot.camera_motion if shot else "",
            shot.focal_subject if shot else "",
            shot.action if shot else "",
            shot.environment if shot else "",
            shot.lighting if shot else "",
            shot.mood if shot else "",
            shot.color_palette if shot else "",
            shot.visual_prompt if shot else "",
            shot_summary if not shot else "",
            ", ".join(theme_tokens),
            "premium generative AI artwork",
            "cinematic lighting",
            "atmospheric depth",
            "rich texture detail",
            "strong subject separation",
            "dynamic motion energy",
        ]
    )
    if include_output_guardrails:
        detail_parts.extend(
            [
                "no text overlay",
                "no subtitles",
                "no watermark",
            ]
        )
    return " | ".join(detail_parts)
