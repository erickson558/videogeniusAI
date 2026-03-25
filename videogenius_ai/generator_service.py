from __future__ import annotations

from dataclasses import replace
import json
import random
import re
from typing import Any, Callable

from .lmstudio_client import LMStudioClient
from .logging_utils import configure_logging
from .models import GenerationRequest, Scene, SceneShot, VideoProject
from .prompt_director import build_cinematic_scene_prompt, build_scene_negative_prompt
from .utils import brief_requests_silent_narration, normalize_search_text, parse_json_payload, safe_float, safe_int

ProgressCallback = Callable[[float, str], None]

LOGGER = configure_logging(__name__)

BRIEF_META_MARKERS = (
    "this prompt",
    "prompt is run",
    "every time",
    "vary the theme",
    "theme randomly",
    "different visual style",
    "visual style each time",
    "cada vez que",
    "cada vez",
    "tema aleatorio",
    "estilo visual",
)
BRIEF_INSTRUCTION_ONLY_PREFIXES = (
    "use ",
    "include ",
    "ensure ",
    "do not ",
    "change ",
    "vary ",
    "usa ",
    "utiliza ",
    "incluye ",
    "asegura ",
    "no ",
    "cambia ",
    "varia ",
)
BRIEF_COMMAND_PREFIXES = (
    "generate",
    "create",
    "make",
    "write",
    "turn",
    "produce",
    "genera",
    "crea",
    "haz",
    "escribe",
    "produce",
)
BRIEF_ROLEPLAY_PREFIXES = (
    "act as",
    "actua como",
    "actúa como",
    "you are",
    "eres",
)
BRIEF_TASK_PREFIXES = (
    "your task is",
    "tu tarea es",
)
BRIEF_SECTION_PREFIXES = (
    "intro",
    "outro",
    "curiosidad",
    "curiosity",
    "estructura del video",
    "video structure",
    "requerimientos de produccion",
    "production requirements",
    "reglas",
    "rules",
)
BRIEF_MEDIA_PATTERN = r"(?:youtube\s+shorts?|short-form\s+video|shorts?|reels?|clips?|cortos?|v[ií]deos?|videos?)"
BRIEF_STYLE_FILLER_TOKENS = {
    "cinematic",
    "cinematographic",
    "cinematografico",
    "cinematografica",
    "fantastico",
    "fantastica",
    "epic",
    "dramatic",
    "dramatico",
    "dramatica",
    "immersive",
    "inmersivo",
    "inmersiva",
    "premium",
    "viral",
    "short",
}
SILENT_BRIEF_PATTERN = re.compile(
    r"\b(?:sin narraci[oó]n|sin voz|no narration|without narration|no voiceover|without voiceover|"
    r"no voice|without voice|silent video|video mudo|mute)\b",
    flags=re.IGNORECASE,
)


class SceneGeneratorService:
    def __init__(self) -> None:
        self.logger = LOGGER

    def _plain_brief_text(self, text: str) -> str:
        plain = re.sub(r"[*`#_]+", " ", str(text or ""))
        plain = re.sub(r"\s+", " ", plain)
        return plain.strip()

    def _brief_lines(self, topic: str) -> list[str]:
        cleaned_lines: list[str] = []
        for raw_line in str(topic or "").splitlines():
            line = raw_line.strip().strip("-* ").strip()
            if not line:
                continue
            line = re.sub(r"^(topic|tema)\s*:\s*", "", line, flags=re.IGNORECASE)
            cleaned_lines.append(self._plain_brief_text(line))
        return cleaned_lines

    def _is_operational_brief_line(self, line: str) -> bool:
        normalized = normalize_search_text(line)
        if any(marker in normalized for marker in BRIEF_META_MARKERS):
            return True
        if normalized.startswith(BRIEF_ROLEPLAY_PREFIXES):
            return True
        if normalized.startswith(BRIEF_TASK_PREFIXES):
            return True
        if normalized.startswith(BRIEF_SECTION_PREFIXES):
            return True
        return normalized.startswith(BRIEF_INSTRUCTION_ONLY_PREFIXES)

    def _extract_structured_topic(self, line: str) -> str:
        candidate = self._plain_brief_text(line)
        patterns = [
            r"\b(?:con|with)\s+(\d+\s+(?:curiosidades?|facts?)\s+[^\.;:]+)",
            r"\b(?:tema|theme)\s*:\s*([^\.;:]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, candidate, flags=re.IGNORECASE)
            if not match:
                continue
            extracted = re.sub(r"\s+", " ", match.group(1)).strip(" .,:;!-")
            if extracted:
                return extracted[:120]
        return ""

    def _clean_brief_candidate(self, line: str) -> str:
        candidate = SILENT_BRIEF_PATTERN.sub(" ", self._plain_brief_text(line))
        candidate = re.sub(
            rf"^(?:please\s+)?(?:{'|'.join(BRIEF_COMMAND_PREFIXES)})\s+",
            "",
            candidate,
            flags=re.IGNORECASE,
        )
        for _ in range(2):
            candidate = re.sub(r"^(?:a|an|the|un|una)\s+", "", candidate, flags=re.IGNORECASE)
            candidate = re.sub(rf"^(?:{BRIEF_MEDIA_PATTERN})\s+", "", candidate, flags=re.IGNORECASE)
            candidate = re.sub(
                rf"^(?:a|an|the|un|una)\s+(?:{BRIEF_MEDIA_PATTERN})\s+",
                "",
                candidate,
                flags=re.IGNORECASE,
            )
        candidate = re.sub(r"^(?:sobre|about|of|de)\s+", "", candidate, flags=re.IGNORECASE)
        split_candidate = re.split(r"\b(?:sobre|about|of)\b", candidate, maxsplit=1, flags=re.IGNORECASE)
        if len(split_candidate) == 2:
            left_tokens = normalize_search_text(split_candidate[0]).split()
            if not left_tokens or all(token in BRIEF_STYLE_FILLER_TOKENS for token in left_tokens):
                candidate = split_candidate[1]
        candidate = re.sub(r"\s+", " ", candidate).strip(" .,:;!-")
        return candidate[:120]

    def _brief_focus(self, topic: str) -> str:
        cleaned_lines = self._brief_lines(topic)
        if not cleaned_lines:
            return "your idea"

        fallback_candidates: list[str] = []
        for line in cleaned_lines:
            structured_candidate = self._extract_structured_topic(line)
            candidate = structured_candidate or self._clean_brief_candidate(line)
            if structured_candidate:
                fallback_candidates.append(structured_candidate)
            elif candidate:
                fallback_candidates.append(candidate)
            if self._is_operational_brief_line(line):
                if structured_candidate:
                    return structured_candidate
                continue
            if candidate:
                return candidate
        return fallback_candidates[0] if fallback_candidates else cleaned_lines[0][:120]

    def _brief_scene_titles(self, topic: str) -> list[str]:
        normalized = normalize_search_text(self._plain_brief_text(topic))
        titles: list[str] = []
        if "intro" in normalized:
            titles.append("Intro")
        curiosity_numbers = sorted(
            {int(match) for match in re.findall(r"\bcuriosidad\s+(\d+)\b|\bcuriosity\s+(\d+)\b", normalized) for match in match if match}
        )
        if curiosity_numbers:
            label = "Curiosidad" if "curiosidad" in normalized else "Curiosity"
            titles.extend(f"{label} {number}" for number in curiosity_numbers)
        if "outro" in normalized:
            titles.append("Outro")
        return titles

    def _infer_total_duration_seconds(self, topic: str) -> int | None:
        plain = self._plain_brief_text(topic)
        match = re.search(r"\b(\d{1,3})\s*(?:segundos?|seconds?)\b", plain, flags=re.IGNORECASE)
        if not match:
            return None
        value = safe_int(match.group(1), 0)
        return value if value > 0 else None

    def _infer_scene_count(self, topic: str) -> int | None:
        explicit_titles = self._brief_scene_titles(topic)
        if explicit_titles:
            return len(explicit_titles)

        normalized = normalize_search_text(self._plain_brief_text(topic))
        match = re.search(r"\b(\d{1,2})\s+curiosidades?\b|\b(\d{1,2})\s+facts?\b", normalized)
        if not match:
            return None
        value = safe_int(next(group for group in match.groups() if group), 0)
        if value <= 0:
            return None
        if "intro" in normalized:
            value += 1
        if "outro" in normalized:
            value += 1
        return value

    def _request_with_brief_overrides(self, request: GenerationRequest) -> GenerationRequest:
        inferred_duration = self._infer_total_duration_seconds(request.topic)
        inferred_scene_count = self._infer_scene_count(request.topic)
        next_duration = inferred_duration or request.total_duration_seconds
        next_scene_count = inferred_scene_count or request.scene_count
        if next_duration == request.total_duration_seconds and next_scene_count == request.scene_count:
            return request
        return replace(
            request,
            total_duration_seconds=next_duration,
            scene_count=next_scene_count,
        )

    def _extract_brief_options(self, topic: str, *markers: str) -> list[str]:
        for raw_line in str(topic or "").splitlines():
            line = raw_line.strip().strip("-* ").strip()
            if not line:
                continue
            lowered = line.casefold()
            for marker in markers:
                marker_lower = marker.casefold()
                if marker_lower not in lowered:
                    continue
                if ":" in line:
                    tail = line.split(":", 1)[1]
                else:
                    tail = line
                options = [
                    item.strip().strip(".")
                    for item in re.split(r",|/|;|\bor\b|\bo\b", tail, flags=re.IGNORECASE)
                    if item.strip()
                ]
                filtered = [item for item in options if len(item) >= 3]
                if filtered:
                    return filtered
        return []

    def _pick_brief_option(self, options: list[str]) -> str:
        if not options:
            return ""
        return random.SystemRandom().choice(options)

    def _theme_seed(self, topic_focus: str) -> str:
        text = normalize_search_text(topic_focus)
        theme_map = {
            ("nature", "naturaleza", "forest", "selva"): "a vast bioluminescent rainforest with towering waterfalls and drifting mist",
            ("technology", "tecnologia", "tech", "ai", "ia", "robot"): "a futuristic AI metropolis filled with holograms, robotics, and glowing data architecture",
            ("food", "comida", "cocina", "kitchen"): "a hyper-detailed gourmet world with sizzling street food, vapor, and cinematic close-ups",
            ("travel", "viaje", "turismo"): "an epic travel montage across neon cities, mountain roads, and impossible skylines",
            ("sports", "deportes", "sport"): "an intense sports arena with explosive motion, sweat, speed, and dramatic lighting",
            ("fantasy", "fantasia", "magic", "magia"): "a mythical fantasy kingdom with colossal ruins, magical energy, and heroic scale",
            ("science fiction", "sci-fi", "ciencia ficcion"): "a science-fiction universe with spacecraft, alien megastructures, and impossible cosmic vistas",
            ("historical", "history", "historico"): "a cinematic historical world with monumental architecture, dense atmosphere, and period detail",
            ("abstract", "arte abstracto", "abstract art"): "an abstract dreamscape of impossible geometry, liquid color, and surreal motion",
        }
        for keys, hint in theme_map.items():
            if any(key in text for key in keys):
                return hint
        return f"a striking cinematic world centered on {topic_focus}"

    def _fallback_language_pack(self, output_language: str) -> dict[str, str | list[str]]:
        normalized = (output_language or "").strip().lower()
        if normalized.startswith("es"):
            return {
                "title_prefix": "Video sobre",
                "summary_template": "Video corto cinematografico sobre {topic}, con un enfoque visual premium y ritmo {tone_lower}.",
                "script": "Introduccion, desarrollo y cierre con llamado a la accion.",
                "structure": "Hook / desarrollo / cierre",
                "scene_titles": ["Gancho", "Contexto", "Desarrollo", "Detalle", "Cierre", "CTA"],
                "description_template": (
                    "Escena {scene_number}: {scene_title_lower} de {topic}, con escala cinematografica y atmosfera impactante. "
                    "Mantén un tono {tone_lower} y un enfoque para {audience_lower}."
                ),
                "narration_template": (
                    "En esta escena {scene_number}, desarrolla {topic} con claridad y ritmo para un {video_format}."
                ),
                "transition": "Corte suave",
            }
        return {
            "title_prefix": "Video about",
            "summary_template": "Cinematic short about {topic}, with premium visual direction and a {tone_lower} pace.",
            "script": "Introduction, development, and closing with a call to action.",
            "structure": "Hook / development / close",
            "scene_titles": ["Hook", "Context", "Development", "Detail", "Close", "CTA"],
            "description_template": (
                "Scene {scene_number}: {scene_title_lower} for {topic}, featuring {theme_seed}. "
                "Keep the tone {tone_lower} and shape it for {audience_lower}."
            ),
            "narration_template": (
                "In scene {scene_number}, develop {topic} clearly and dynamically for a {video_format}."
            ),
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
        topic_excerpt = self._brief_focus(request.topic).replace("\n", " ")[:140]
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
        request = self._request_with_brief_overrides(request)
        pack = self._fallback_language_pack(request.output_language)
        scene_titles = self._brief_scene_titles(request.topic) or pack["scene_titles"]
        assert isinstance(scene_titles, list)
        silent_narration = brief_requests_silent_narration(request.topic)

        scenes: list[dict[str, Any]] = []
        default_duration = max(1, request.total_duration_seconds // max(1, request.scene_count))
        theme_options = self._extract_brief_options(
            request.topic,
            "vary the theme randomly",
            "tema aleatorio",
            "theme randomly",
        )
        style_options = self._extract_brief_options(
            request.topic,
            "use a different visual style each time",
            "visual style each time",
            "estilo visual",
        )
        short_topic = self._pick_brief_option(theme_options) or self._brief_focus(request.topic)
        style_hint = self._pick_brief_option(style_options)
        effective_visual_style = request.visual_style if not style_hint else f"{request.visual_style}, {style_hint}"
        theme_seed = self._theme_seed(short_topic)
        fallback_request = GenerationRequest(
            topic=short_topic,
            visual_style=effective_visual_style,
            audience=request.audience,
            narrative_tone=request.narrative_tone,
            video_format=request.video_format,
            output_language=request.output_language,
            total_duration_seconds=request.total_duration_seconds,
            scene_count=request.scene_count,
            generation_mode=request.generation_mode,
            model=request.model,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
        )

        for index in range(request.scene_count):
            title = str(scene_titles[min(index, len(scene_titles) - 1)])
            description = str(pack["description_template"]).format(
                scene_number=index + 1,
                scene_title=title,
                scene_title_lower=title.lower(),
                topic=short_topic,
                theme_seed=theme_seed,
                tone_lower=request.narrative_tone.lower(),
                audience_lower=request.audience.lower(),
                video_format=request.video_format,
            )
            direction = self._fallback_scene_direction(fallback_request, index)
            narration = ""
            if not silent_narration:
                narration = str(pack["narration_template"]).format(
                    scene_number=index + 1,
                    scene_title=title,
                    scene_title_lower=title.lower(),
                    topic=short_topic,
                    theme_seed=theme_seed,
                    tone_lower=request.narrative_tone.lower(),
                    audience_lower=request.audience.lower(),
                    video_format=request.video_format,
                )
            visual_prompt = (
                f"{effective_visual_style}, {request.video_format}, scene {index + 1}, {short_topic}, {theme_seed}, "
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
                        request=fallback_request,
                        scene_index=index,
                        scene_title=title,
                        scene_description=description,
                        scene_duration=default_duration,
                    ),
                }
            )

        payload = {
            "title": f"{pack['title_prefix']} {short_topic[:60]}",
            "summary": str(pack["summary_template"]).format(
                topic=short_topic,
                theme_seed=theme_seed,
                tone_lower=request.narrative_tone.lower(),
                audience_lower=request.audience.lower(),
                video_format=request.video_format,
            ),
            "general_script": str(pack["script"]),
            "structure": str(pack["structure"]),
            "scenes": scenes,
        }
        project = self.normalize_project(payload, request, raw_response='{"source":"local-fallback"}')
        self.logger.warning("Using local fallback project generation because LM Studio was unavailable.")
        return project

    def build_messages(self, request: GenerationRequest, previous_response: str = "") -> list[dict[str, str]]:
        request = self._request_with_brief_overrides(request)
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
            "Keep scene numbering sequential and durations realistic. "
            "Treat the user input as a creative brief, not as narration to repeat literally."
        )

        topic_focus = self._brief_focus(request.topic)
        silent_narration = brief_requests_silent_narration(request.topic)
        structured_scene_titles = self._brief_scene_titles(request.topic)
        user_message = (
            "Create a video project in JSON for the following request.\n"
            f"Primary theme to develop: {topic_focus}\n"
            f"Source brief:\n{request.topic}\n"
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
            "- Build the video around one clear theme derived from the brief instead of repeating the brief word for word.\n"
            "- Do not narrate operational prompt phrases like 'generate a unique YouTube Shorts video', 'this prompt', or 'every time this prompt is run' unless the requested topic is explicitly prompt engineering.\n"
            "- For each scene, include cinematic_intent, camera_language, lighting_style, color_palette, energy_level, and negative_prompt.\n"
            "- Unless the mode is 'Solo guion', include 2 to 4 shots per scene with concrete camera direction and visually imaginative prompts.\n"
            "- Make the visuals feel premium, vivid, and cinematic instead of generic stock footage.\n"
            "- Do not output <think>, analysis, explanations, or markdown fences.\n"
            "- Return strict JSON with this structure:\n"
            f"{json.dumps(schema, ensure_ascii=False, indent=2)}"
        )
        if silent_narration:
            user_message += "\n- The brief explicitly requests no narration. Set narration to an empty string for every scene.\n"
        else:
            user_message += "\n- Make narration ready to read aloud.\n"
        if structured_scene_titles:
            user_message += (
                "\n- The brief already defines an explicit multi-part outline. Preserve that outline scene by scene "
                f"using this order: {', '.join(structured_scene_titles)}.\n"
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
        if brief_requests_silent_narration(request.topic):
            scene.narration = ""
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
        request = self._request_with_brief_overrides(request)
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
            title=str(payload.get("title") or payload.get("video_title") or self._brief_focus(request.topic)[:80]),
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
        effective_request = self._request_with_brief_overrides(request)
        if effective_request != request:
            self.logger.info(
                "Applied brief overrides | scene_count=%s->%s | duration_seconds=%s->%s | focus=%s",
                request.scene_count,
                effective_request.scene_count,
                request.total_duration_seconds,
                effective_request.total_duration_seconds,
                self._brief_focus(request.topic),
            )
        self.logger.info(
            "Starting LM Studio project generation | model=%s | scenes=%s | duration_seconds=%s | language=%s | mode=%s",
            effective_request.model or "<auto>",
            effective_request.scene_count,
            effective_request.total_duration_seconds,
            effective_request.output_language,
            effective_request.generation_mode,
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
                    model=effective_request.model,
                    messages=self.build_messages(effective_request, previous_response),
                    temperature=effective_request.temperature,
                    max_tokens=effective_request.max_tokens,
                    expect_json=True,
                )

                if progress_callback:
                    progress_callback(0.75, "Parsing JSON response...")

                payload = parse_json_payload(raw_response)
                project = self.normalize_project(payload, effective_request, raw_response)
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
