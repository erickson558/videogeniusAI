from __future__ import annotations

import json
from typing import Any, Callable

from .lmstudio_client import LMStudioClient
from .logging_utils import configure_logging
from .models import GenerationRequest, Scene, SceneShot, VideoProject
from .prompt_director import build_cinematic_scene_prompt, build_scene_negative_prompt
from .utils import parse_json_payload, safe_float, safe_int

ProgressCallback = Callable[[float, str], None]

LOGGER = configure_logging(__name__)


class SceneGeneratorService:
    def __init__(self) -> None:
        self.logger = LOGGER

    def _fallback_language_pack(self, output_language: str) -> dict[str, str | list[str]]:
        normalized = (output_language or "").strip().lower()
        if normalized.startswith("es"):
            return {
                "title_prefix": "Video sobre",
                "summary_prefix": "Resumen automatizado sobre",
                "script": "Introduccion, desarrollo y cierre con llamado a la accion.",
                "structure": "Hook / desarrollo / cierre",
                "scene_titles": ["Gancho", "Contexto", "Desarrollo", "Detalle", "Cierre", "CTA"],
                "description_prefix": "Escena",
                "narration_prefix": "En esta escena",
                "transition": "Corte suave",
            }
        return {
            "title_prefix": "Video about",
            "summary_prefix": "Automatic summary about",
            "script": "Introduction, development, and closing with a call to action.",
            "structure": "Hook / development / close",
            "scene_titles": ["Hook", "Context", "Development", "Detail", "Close", "CTA"],
            "description_prefix": "Scene",
            "narration_prefix": "In this scene",
            "transition": "Smooth cut",
        }

    def _fallback_scene_direction(self, request: GenerationRequest, scene_index: int) -> dict[str, str]:
        lighting_options = [
            "volumetric golden light with dramatic depth",
            "electric neon glow with reflective highlights",
            "storm-lit atmosphere with contrasty clouds",
            "misty sunrise haze with soft bloom",
        ]
        palette_options = [
            "teal, amber, obsidian black",
            "electric blue, magenta, chrome",
            "emerald, cyan, silver",
            "sunset orange, violet, deep navy",
        ]
        camera_options = [
            "wide cinematic establishing shots with layered depth",
            "dynamic dolly moves and parallax reveals",
            "sweeping crane motion with scale emphasis",
            "immersive orbiting camera language with energy",
        ]
        energy_options = ["slow-burn", "rising", "charged", "epic"]
        intent_options = [
            "show a world that feels larger than life",
            "make the visuals feel premium and emotionally immersive",
            "present the idea with wonder, scale, and vivid atmosphere",
            "turn the concept into a striking faceless cinematic sequence",
        ]
        return {
            "lighting_style": lighting_options[scene_index % len(lighting_options)],
            "color_palette": palette_options[scene_index % len(palette_options)],
            "camera_language": camera_options[scene_index % len(camera_options)],
            "energy_level": energy_options[scene_index % len(energy_options)],
            "cinematic_intent": intent_options[scene_index % len(intent_options)],
        }

    def _fallback_shots_for_scene(
        self,
        *,
        request: GenerationRequest,
        scene_index: int,
        scene_title: str,
        scene_description: str,
        scene_duration: int,
    ) -> list[dict[str, Any]]:
        shot_templates = [
            ("ultra wide establishing shot", "low angle", "slow push-in"),
            ("medium cinematic shot", "eye level", "gentle lateral slide"),
            ("hero detail shot", "close perspective", "micro orbit"),
            ("scale reveal shot", "high angle", "crane rise"),
        ]
        shot_count = 2 if scene_duration <= 4 else 3 if scene_duration <= 10 else 4
        base_duration = max(0.8, scene_duration / max(1, shot_count))
        shots: list[dict[str, Any]] = []
        topic_excerpt = request.topic.strip().replace("\n", " ")[:140]
        for shot_index in range(shot_count):
            shot_type, camera_angle, camera_motion = shot_templates[(scene_index + shot_index) % len(shot_templates)]
            shots.append(
                {
                    "shot_number": shot_index + 1,
                    "duration_seconds": round(base_duration, 2),
                    "shot_type": shot_type,
                    "camera_angle": camera_angle,
                    "camera_motion": camera_motion,
                    "focal_subject": f"{scene_title} inspired by {topic_excerpt}",
                    "action": f"visual escalation of {scene_description}",
                    "environment": f"{request.visual_style} environment for a {request.video_format}",
                    "lighting": self._fallback_scene_direction(request, scene_index + shot_index)["lighting_style"],
                    "mood": request.narrative_tone,
                    "color_palette": self._fallback_scene_direction(request, scene_index + shot_index)["color_palette"],
                    "visual_prompt": (
                        f"{request.visual_style}, {scene_title}, {shot_type}, {camera_motion}, "
                        f"{scene_description}, imaginative environment, premium generative AI look"
                    ),
                }
            )
        return shots

    def generate_fallback_project(self, request: GenerationRequest) -> VideoProject:
        pack = self._fallback_language_pack(request.output_language)
        scene_titles = pack["scene_titles"]
        assert isinstance(scene_titles, list)

        scenes: list[dict[str, Any]] = []
        default_duration = max(1, request.total_duration_seconds // max(1, request.scene_count))
        topic_excerpt = request.topic.strip().replace("\n", " ")
        short_topic = topic_excerpt[:120] or "your idea"

        for index in range(request.scene_count):
            title = str(scene_titles[min(index, len(scene_titles) - 1)])
            description = (
                f"{pack['description_prefix']} {index + 1} focused on {short_topic}. "
                f"Keep the tone {request.narrative_tone.lower()} and adapt it for {request.audience.lower()}."
            )
            direction = self._fallback_scene_direction(request, index)
            narration = (
                f"{pack['narration_prefix']} {index + 1}, present a clear part of {short_topic} "
                f"for a {request.video_format} in {request.output_language}."
            )
            visual_prompt = (
                f"{request.visual_style}, {request.video_format}, scene {index + 1}, {short_topic}, "
                f"cinematic composition, {direction['lighting_style']}, {direction['color_palette']}, high detail"
            )
            scenes.append(
                {
                    "scene_number": index + 1,
                    "scene_title": title,
                    "description": description,
                    "visual_description": description,
                    "visual_prompt": visual_prompt,
                    "narration": narration,
                    "duration_seconds": default_duration,
                    "transition": str(pack["transition"]),
                    "cinematic_intent": direction["cinematic_intent"],
                    "camera_language": direction["camera_language"],
                    "lighting_style": direction["lighting_style"],
                    "color_palette": direction["color_palette"],
                    "energy_level": direction["energy_level"],
                    "negative_prompt": "",
                    "shots": self._fallback_shots_for_scene(
                        request=request,
                        scene_index=index,
                        scene_title=title,
                        scene_description=description,
                        scene_duration=default_duration,
                    ),
                }
            )

        payload = {
            "title": f"{pack['title_prefix']} {short_topic[:60]}",
            "summary": f"{pack['summary_prefix']} {short_topic}.",
            "general_script": str(pack["script"]),
            "structure": str(pack["structure"]),
            "scenes": scenes,
        }
        project = self.normalize_project(payload, request, raw_response='{"source":"local-fallback"}')
        self.logger.warning("Using local fallback project generation because LM Studio was unavailable.")
        return project

    def build_messages(self, request: GenerationRequest, previous_response: str = "") -> list[dict[str, str]]:
        schema = {
            "title": "string",
            "summary": "string",
            "general_script": "string",
            "structure": "string",
            "estimated_total_duration_seconds": request.total_duration_seconds,
            "scenes": [
                {
                    "scene_number": 1,
                    "scene_title": "string",
                    "description": "string",
                    "visual_description": "string",
                    "visual_prompt": "string",
                    "narration": "string",
                    "duration_seconds": 8,
                    "transition": "string",
                    "cinematic_intent": "string",
                    "camera_language": "string",
                    "lighting_style": "string",
                    "color_palette": "string",
                    "energy_level": "string",
                    "negative_prompt": "string",
                    "shots": [
                        {
                            "shot_number": 1,
                            "duration_seconds": 2.5,
                            "shot_type": "string",
                            "camera_angle": "string",
                            "camera_motion": "string",
                            "focal_subject": "string",
                            "action": "string",
                            "environment": "string",
                            "lighting": "string",
                            "mood": "string",
                            "color_palette": "string",
                            "visual_prompt": "string",
                        }
                    ],
                }
            ],
        }

        mode_rules = {
            "Solo guion": (
                "Focus on title, summary, structure, narration, scene description, and timing. "
                "Set visual_description and visual_prompt to empty strings."
            ),
            "Guion + prompts": (
                "Include script details plus strong visual_description and visual_prompt per scene."
            ),
            "Proyecto completo": (
                "Include everything needed for a production-ready storyboard package with strong visual prompts, "
                "clear narration, and scene transitions."
            ),
        }

        system_message = (
            "You are a senior video strategist and storyboard writer. "
            "Think like a cinematic AI film director for faceless, visually rich short videos. "
            "Return only valid JSON. No markdown. No commentary. "
            "Do not include reasoning, analysis, or <think> blocks. "
            "Keep scene numbering sequential and durations realistic."
        )

        user_message = (
            "Create a video project in JSON for the following request.\n"
            f"Topic: {request.topic}\n"
            f"Visual style: {request.visual_style}\n"
            f"Audience: {request.audience}\n"
            f"Narrative tone: {request.narrative_tone}\n"
            f"Video format: {request.video_format}\n"
            f"Output language: {request.output_language}\n"
            f"Scene count: {request.scene_count}\n"
            f"Total estimated duration in seconds: {request.total_duration_seconds}\n"
            f"Generation mode: {request.generation_mode}\n"
            f"Mode rules: {mode_rules.get(request.generation_mode, mode_rules['Proyecto completo'])}\n"
            "Requirements:\n"
            "- Keep the language consistent with the requested output language.\n"
            "- Distribute the total duration across all scenes.\n"
            "- Make scene titles concise.\n"
            "- Make narration ready to read aloud.\n"
            "- For each scene, include cinematic_intent, camera_language, lighting_style, color_palette, energy_level, and negative_prompt.\n"
            "- Unless the mode is 'Solo guion', include 2 to 4 shots per scene with concrete camera direction and visually imaginative prompts.\n"
            "- Make the visuals feel premium, vivid, and cinematic instead of generic stock footage.\n"
            "- Do not output <think>, analysis, explanations, or markdown fences.\n"
            "- Return strict JSON with this structure:\n"
            f"{json.dumps(schema, ensure_ascii=False, indent=2)}"
        )

        if previous_response:
            user_message += (
                "\nThe previous answer was not parseable JSON. "
                "Rewrite it as strict JSON only. Remove markdown, comments, or trailing text.\n"
                f"Previous answer:\n{previous_response}"
            )

        return [
            {"role": "system", "content": system_message},
            {"role": "user", "content": user_message},
        ]

    def _normalize_shot(
        self,
        item: dict[str, Any],
        index: int,
        default_duration: float,
    ) -> SceneShot:
        return SceneShot(
            shot_number=safe_int(
                item.get("shot_number") or item.get("number") or item.get("shot"),
                index + 1,
            ),
            duration_seconds=max(
                0.4,
                safe_float(item.get("duration_seconds") or item.get("duration"), default_duration),
            ),
            shot_type=str(item.get("shot_type") or item.get("framing") or ""),
            camera_angle=str(item.get("camera_angle") or item.get("angle") or ""),
            camera_motion=str(item.get("camera_motion") or item.get("motion") or ""),
            focal_subject=str(item.get("focal_subject") or item.get("subject") or ""),
            action=str(item.get("action") or item.get("movement") or ""),
            environment=str(item.get("environment") or item.get("setting") or ""),
            lighting=str(item.get("lighting") or item.get("lighting_style") or ""),
            mood=str(item.get("mood") or item.get("emotion") or ""),
            color_palette=str(item.get("color_palette") or item.get("palette") or ""),
            visual_prompt=str(item.get("visual_prompt") or item.get("prompt") or ""),
        )

    def _rebalance_shot_durations(self, shots: list[SceneShot], total_duration: int) -> None:
        if not shots:
            return

        current_total = sum(max(0.4, shot.duration_seconds) for shot in shots)
        if current_total <= 0:
            even_duration = max(0.8, total_duration / max(1, len(shots)))
            for shot in shots:
                shot.duration_seconds = even_duration
            return

        scale = total_duration / current_total
        adjusted = [max(0.4, round(shot.duration_seconds * scale, 2)) for shot in shots]
        difference = round(total_duration - sum(adjusted), 2)
        adjusted[-1] = max(0.4, round(adjusted[-1] + difference, 2))
        for shot, duration in zip(shots, adjusted):
            shot.duration_seconds = duration

    def _normalize_shots(self, item: dict[str, Any], request: GenerationRequest, scene: Scene, index: int) -> list[SceneShot]:
        raw_shots = item.get("shots") or item.get("shot_plan") or item.get("beats") or []
        default_duration = max(0.8, scene.duration_seconds / max(1, min(4, max(1, round(scene.duration_seconds / 2.5)))))
        shots = [
            self._normalize_shot(shot_item, shot_index, default_duration)
            for shot_index, shot_item in enumerate(raw_shots)
            if isinstance(shot_item, dict)
        ]
        if request.generation_mode != "Solo guion" and not shots:
            fallback = self._fallback_shots_for_scene(
                request=request,
                scene_index=index,
                scene_title=scene.scene_title,
                scene_description=scene.visual_description or scene.description or scene.scene_title,
                scene_duration=scene.duration_seconds,
            )
            shots = [
                self._normalize_shot(shot_item, shot_index, default_duration)
                for shot_index, shot_item in enumerate(fallback)
            ]
        self._rebalance_shot_durations(shots, scene.duration_seconds)
        return shots

    def _normalize_scene(self, item: dict[str, Any], index: int, default_duration: int, request: GenerationRequest) -> Scene:
        scene = Scene(
            scene_number=safe_int(
                item.get("scene_number") or item.get("number") or item.get("scene"),
                index + 1,
            ),
            scene_title=str(item.get("scene_title") or item.get("title") or f"Scene {index + 1}"),
            description=str(item.get("description") or item.get("scene_description") or ""),
            visual_description=str(item.get("visual_description") or item.get("shot_description") or ""),
            visual_prompt=str(item.get("visual_prompt") or item.get("prompt") or ""),
            narration=str(item.get("narration") or item.get("voiceover") or ""),
            duration_seconds=max(
                1,
                safe_int(item.get("duration_seconds") or item.get("duration"), default_duration),
            ),
            transition=str(item.get("transition") or item.get("transition_to_next") or ""),
            cinematic_intent=str(item.get("cinematic_intent") or item.get("intent") or ""),
            camera_language=str(item.get("camera_language") or item.get("camera_style") or ""),
            lighting_style=str(item.get("lighting_style") or item.get("lighting") or ""),
            color_palette=str(item.get("color_palette") or item.get("palette") or ""),
            energy_level=str(item.get("energy_level") or item.get("energy") or ""),
            negative_prompt=str(item.get("negative_prompt") or ""),
        )
        scene.shots = self._normalize_shots(item, request, scene, index)

        if request.generation_mode == "Solo guion":
            scene.visual_description = ""
            scene.visual_prompt = ""
            scene.cinematic_intent = ""
            scene.camera_language = ""
            scene.lighting_style = ""
            scene.color_palette = ""
            scene.energy_level = ""
            scene.negative_prompt = ""
            scene.shots = []
        return scene

    def _rebalance_durations(self, scenes: list[Scene], total_duration: int) -> None:
        if not scenes:
            return

        current_total = sum(max(1, scene.duration_seconds) for scene in scenes)
        if current_total <= 0:
            each = max(1, total_duration // len(scenes))
            for scene in scenes:
                scene.duration_seconds = each
            current_total = sum(scene.duration_seconds for scene in scenes)

        # Keep the requested total duration stable even when the model returns uneven values.
        scale = total_duration / current_total if current_total else 1
        adjusted = [max(1, round(scene.duration_seconds * scale)) for scene in scenes]
        difference = total_duration - sum(adjusted)
        adjusted[-1] = max(1, adjusted[-1] + difference)

        for scene, duration in zip(scenes, adjusted):
            scene.duration_seconds = duration

    def normalize_project(self, payload: dict[str, Any], request: GenerationRequest, raw_response: str) -> VideoProject:
        raw_scenes = payload.get("scenes")
        if not isinstance(raw_scenes, list) or not raw_scenes:
            raise ValueError("The JSON payload does not include a valid 'scenes' list.")

        default_duration = max(1, request.total_duration_seconds // max(1, request.scene_count))
        scenes = [
            self._normalize_scene(item, index, default_duration, request)
            for index, item in enumerate(raw_scenes)
            if isinstance(item, dict)
        ]
        if not scenes:
            raise ValueError("No usable scenes were returned by LM Studio.")

        self._rebalance_durations(scenes, request.total_duration_seconds)

        project = VideoProject(
            title=str(payload.get("title") or payload.get("video_title") or request.topic[:80]),
            summary=str(payload.get("summary") or payload.get("overview") or ""),
            general_script=str(payload.get("general_script") or payload.get("script") or ""),
            structure=str(payload.get("structure") or payload.get("video_structure") or ""),
            estimated_total_duration_seconds=request.total_duration_seconds,
            output_language=request.output_language,
            generation_mode=request.generation_mode,
            source_topic=request.topic,
            visual_style=request.visual_style,
            audience=request.audience,
            narrative_tone=request.narrative_tone,
            video_format=request.video_format,
            scenes=scenes,
            raw_response=raw_response,
        )
        self._enrich_project(project)
        return project

    def _enrich_project(self, project: VideoProject) -> None:
        for index, scene in enumerate(project.scenes):
            direction = self._fallback_scene_direction(
                GenerationRequest(
                    topic=project.source_topic,
                    visual_style=project.visual_style,
                    audience=project.audience,
                    narrative_tone=project.narrative_tone,
                    video_format=project.video_format,
                    output_language=project.output_language,
                    total_duration_seconds=project.estimated_total_duration_seconds,
                    scene_count=max(1, len(project.scenes)),
                    generation_mode=project.generation_mode,
                    model="",
                    temperature=0.7,
                    max_tokens=0,
                ),
                index,
            )
            if project.generation_mode == "Solo guion":
                scene.shots = []
                continue
            if not scene.cinematic_intent:
                scene.cinematic_intent = direction["cinematic_intent"]
            if not scene.camera_language:
                scene.camera_language = direction["camera_language"]
            if not scene.lighting_style:
                scene.lighting_style = direction["lighting_style"]
            if not scene.color_palette:
                scene.color_palette = direction["color_palette"]
            if not scene.energy_level:
                scene.energy_level = direction["energy_level"]
            if not scene.shots:
                scene.shots = [
                    self._normalize_shot(
                        shot_item,
                        shot_index,
                        max(0.8, scene.duration_seconds / 3),
                    )
                    for shot_index, shot_item in enumerate(
                        self._fallback_shots_for_scene(
                            request=GenerationRequest(
                                topic=project.source_topic,
                                visual_style=project.visual_style,
                                audience=project.audience,
                                narrative_tone=project.narrative_tone,
                                video_format=project.video_format,
                                output_language=project.output_language,
                                total_duration_seconds=project.estimated_total_duration_seconds,
                                scene_count=max(1, len(project.scenes)),
                                generation_mode=project.generation_mode,
                                model="",
                                temperature=0.7,
                                max_tokens=0,
                            ),
                            scene_index=index,
                            scene_title=scene.scene_title,
                            scene_description=scene.visual_description or scene.description or scene.scene_title,
                            scene_duration=scene.duration_seconds,
                        )
                    )
                ]
            self._rebalance_shot_durations(scene.shots, scene.duration_seconds)
            if not scene.visual_prompt:
                scene.visual_prompt = build_cinematic_scene_prompt(project, scene, aspect_ratio="9:16")
            if not scene.negative_prompt:
                scene.negative_prompt = build_scene_negative_prompt("")
            for shot in scene.shots:
                if not shot.visual_prompt:
                    shot.visual_prompt = build_cinematic_scene_prompt(
                        project,
                        scene,
                        aspect_ratio="9:16",
                        shot=shot,
                    )

    def generate(
        self,
        *,
        client: LMStudioClient,
        request: GenerationRequest,
        retry_attempts: int,
        progress_callback: ProgressCallback | None = None,
    ) -> VideoProject:
        self.logger.info(
            "Starting LM Studio project generation | model=%s | scenes=%s | duration_seconds=%s | language=%s | mode=%s",
            request.model or "<auto>",
            request.scene_count,
            request.total_duration_seconds,
            request.output_language,
            request.generation_mode,
        )
        if progress_callback:
            progress_callback(0.05, "Preparing generation prompt...")

        previous_response = ""
        last_error: Exception | None = None

        for attempt in range(1, retry_attempts + 1):
            try:
                if progress_callback:
                    progress_callback(
                        min(0.1 + (attempt - 1) * 0.25, 0.85),
                        f"Calling LM Studio (attempt {attempt}/{retry_attempts})...",
                    )

                raw_response = client.chat_completion(
                    model=request.model,
                    messages=self.build_messages(request, previous_response),
                    temperature=request.temperature,
                    max_tokens=request.max_tokens,
                    expect_json=True,
                )

                if progress_callback:
                    progress_callback(0.75, "Parsing JSON response...")

                payload = parse_json_payload(raw_response)
                project = self.normalize_project(payload, request, raw_response)
                self.logger.info(
                    "Project generation completed | title=%s | scenes=%s",
                    project.title,
                    len(project.scenes),
                )

                if progress_callback:
                    progress_callback(1.0, "Project generated successfully.")
                return project
            except Exception as exc:
                last_error = exc
                previous_response = locals().get("raw_response", "")
                self.logger.warning("Generation attempt %s failed: %s", attempt, exc)

        assert last_error is not None
        self.logger.error("Project generation exhausted all retries | attempts=%s", retry_attempts)
        raise last_error
