from __future__ import annotations

import json
from typing import Any, Callable

from .lmstudio_client import LMStudioClient
from .logging_utils import configure_logging
from .models import GenerationRequest, Scene, VideoProject
from .utils import parse_json_payload, safe_int

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
            narration = (
                f"{pack['narration_prefix']} {index + 1}, present a clear part of {short_topic} "
                f"for a {request.video_format} in {request.output_language}."
            )
            visual_prompt = (
                f"{request.visual_style}, {request.video_format}, scene {index + 1}, {short_topic}, "
                f"cinematic composition, high detail"
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
        )

        if request.generation_mode == "Solo guion":
            scene.visual_description = ""
            scene.visual_prompt = ""
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
        return project

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
