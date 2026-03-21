from __future__ import annotations

import math
import subprocess
import textwrap
from pathlib import Path
from shutil import which

from PIL import Image, ImageDraw, ImageFont

from .comfyui_client import ComfyUIClient, detect_workflow_output_mode
from .logging_utils import configure_logging
from .models import RenderedVideoResult, SceneShot, VideoProject, VideoRenderRequest
from .prompt_director import build_cinematic_scene_prompt, build_scene_negative_prompt, summarize_scene_shots
from .tts_service import PiperTTSService, WindowsTTSService
from .utils import now_stamp, sanitize_filename

CREATE_NO_WINDOW = 0x08000000
SHORTS_FPS = 25
ASPECT_RATIO_DIMENSIONS = {
    "9:16": (720, 1280),
    "16:9": (1280, 720),
    "1:1": (1080, 1080),
}
LOGGER = configure_logging(__name__)


def _pick_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        Path("C:/Windows/Fonts/segoeuib.ttf"),
        Path("C:/Windows/Fonts/segoeui.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def _ffmpeg_escape_path(path: str | Path) -> str:
    text = str(Path(path).resolve()).replace("\\", "/")
    return text.replace(":", "\\:")


class StoryboardVideoService:
    def __init__(self) -> None:
        self.logger = LOGGER

    def _dimensions_for_ratio(self, aspect_ratio: str) -> tuple[int, int]:
        return ASPECT_RATIO_DIMENSIONS.get(aspect_ratio, ASPECT_RATIO_DIMENSIONS["9:16"])

    def _resolve_ffmpeg_paths(self, ffmpeg_path: str) -> tuple[str, str]:
        resolved_ffmpeg = ffmpeg_path.strip() if ffmpeg_path.strip() and Path(ffmpeg_path).exists() else (which("ffmpeg") or which("ffmpeg.exe"))
        if not resolved_ffmpeg:
            raise FileNotFoundError("FFmpeg is not available in PATH.")

        sibling = Path(resolved_ffmpeg).with_name("ffprobe.exe")
        resolved_ffprobe = str(sibling.resolve()) if sibling.exists() else ""
        if not resolved_ffprobe:
            resolved_ffprobe = which("ffprobe") or which("ffprobe.exe") or ""
        if not resolved_ffprobe:
            raise FileNotFoundError("FFprobe is not available in PATH.")
        return resolved_ffmpeg, resolved_ffprobe

    def _run(self, command: list[str]) -> None:
        subprocess.run(
            command,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=CREATE_NO_WINDOW,
        )

    def _media_duration(self, ffprobe_path: str, file_path: str | Path) -> float:
        result = subprocess.run(
            [
                ffprobe_path,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(file_path),
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=CREATE_NO_WINDOW,
            text=True,
        )
        return max(0.1, float(result.stdout.strip()))

    def _scene_prompt(self, project: VideoProject, scene_index: int, aspect_ratio: str, shot: SceneShot | None = None) -> str:
        return build_cinematic_scene_prompt(
            project,
            project.scenes[scene_index],
            aspect_ratio=aspect_ratio,
            shot=shot,
        )

    def _scene_negative_prompt(self, request: VideoRenderRequest, scene_index: int) -> str:
        scene = request.project.scenes[scene_index]
        return build_scene_negative_prompt(request.comfyui_negative_prompt, scene.negative_prompt)

    def _scene_shots(self, project: VideoProject, scene_index: int, target_duration: float) -> list[SceneShot]:
        scene = project.scenes[scene_index]
        source = scene.shots or [
            SceneShot(
                shot_number=1,
                duration_seconds=target_duration,
                shot_type="hero cinematic shot",
                camera_angle=scene.camera_language,
                camera_motion="slow push-in",
                focal_subject=scene.scene_title or project.title,
                action=scene.visual_description or scene.description,
                environment=project.visual_style,
                lighting=scene.lighting_style,
                mood=scene.energy_level or project.narrative_tone,
                color_palette=scene.color_palette,
                visual_prompt=scene.visual_prompt,
            )
        ]
        shots = [SceneShot.from_dict(shot.to_dict(), index) for index, shot in enumerate(source)]
        total = sum(max(0.4, shot.duration_seconds) for shot in shots)
        if total <= 0:
            even_duration = max(0.8, target_duration / max(1, len(shots)))
            for shot in shots:
                shot.duration_seconds = even_duration
            return shots
        scale = target_duration / total
        adjusted = [max(0.4, round(shot.duration_seconds * scale, 2)) for shot in shots]
        difference = round(target_duration - sum(adjusted), 2)
        adjusted[-1] = max(0.4, round(adjusted[-1] + difference, 2))
        for shot, duration in zip(shots, adjusted):
            shot.duration_seconds = duration
        return shots

    def _scene_caption(self, project: VideoProject, scene_index: int) -> str:
        scene = project.scenes[scene_index]
        return (scene.narration or scene.description or scene.scene_title or project.title).strip()

    def _scene_audio(self, request: VideoRenderRequest, scene_index: int, audio_dir: Path) -> Path | None:
        narration = self._scene_caption(request.project, scene_index)
        if not narration:
            return None

        if request.tts_backend == "Piper local":
            tts = PiperTTSService(
                executable_path=request.piper_executable_path,
                model_path=request.piper_model_path,
            )
        elif request.tts_backend == "Windows local":
            tts = WindowsTTSService()
        else:
            return None

        output_path = audio_dir / f"scene_{request.project.scenes[scene_index].scene_number:02d}.wav"
        return tts.synthesize(narration, output_path)

    def _write_scene_subtitle(self, caption_text: str, duration_seconds: float, subtitle_path: Path) -> Path:
        subtitle_path.parent.mkdir(parents=True, exist_ok=True)
        words = [word for word in (caption_text or "").split() if word.strip()]
        if not words:
            subtitle_path.write_text("", encoding="utf-8")
            return subtitle_path

        chunk_size = 4 if len(words) <= 12 else 5
        chunks = [" ".join(words[index:index + chunk_size]) for index in range(0, len(words), chunk_size)]
        segment_duration = max(0.6, duration_seconds / max(1, len(chunks)))

        def format_timestamp(value: float) -> str:
            total_milliseconds = max(0, int(round(value * 1000)))
            hours = total_milliseconds // 3_600_000
            remainder = total_milliseconds % 3_600_000
            minutes = remainder // 60_000
            remainder %= 60_000
            seconds = remainder // 1000
            milliseconds = remainder % 1000
            return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"

        entries: list[str] = []
        for index, chunk in enumerate(chunks, start=1):
            start_time = (index - 1) * segment_duration
            end_time = duration_seconds if index == len(chunks) else min(duration_seconds, index * segment_duration)
            wrapped = textwrap.fill(chunk, width=18)
            entries.append(
                f"{index}\n{format_timestamp(start_time)} --> {format_timestamp(end_time)}\n{wrapped}\n"
            )

        subtitle_path.write_text("\n".join(entries), encoding="utf-8")
        return subtitle_path

    def _subtitle_filter(self, subtitle_path: Path, width: int, height: int) -> str:
        font_size = max(16, int(width * 0.032))
        margin_v = max(40, int(height * 0.055))
        style = ",".join(
            [
                "FontName=Arial",
                "Bold=1",
                f"FontSize={font_size}",
                "PrimaryColour=&H00FFFFFF",
                "OutlineColour=&H00101010",
                "BackColour=&H78000000",
                "BorderStyle=3",
                "Outline=2",
                "Shadow=0",
                "Alignment=2",
                f"MarginV={margin_v}",
            ]
        )
        return f"subtitles='{_ffmpeg_escape_path(subtitle_path)}':force_style='{style}'"

    def _motion_profile(self, scene_index: int) -> tuple[float, float, float, float]:
        profiles = [
            (0.08, 0.20, 0.08, 0.14),
            (0.22, 0.08, 0.10, 0.18),
            (0.10, 0.16, 0.22, 0.08),
            (0.18, 0.10, 0.14, 0.20),
        ]
        return profiles[scene_index % len(profiles)]

    def _build_scene_clip(
        self,
        *,
        ffmpeg_path: str,
        image_path: Path,
        audio_path: Path | None,
        target_duration: float,
        width: int,
        height: int,
        scene_index: int,
        subtitle_path: Path | None,
        output_path: Path,
    ) -> Path:
        frames = max(1, math.ceil(target_duration * SHORTS_FPS))
        overscan_w = width + max(120, width // 6)
        overscan_h = height + max(120, height // 6)
        start_x, end_x, start_y, end_y = self._motion_profile(scene_index)
        delta_x = end_x - start_x
        delta_y = end_y - start_y
        zoom_speed = 0.0007 + ((scene_index % 3) * 0.0001)
        filters = [
            f"scale={overscan_w}:{overscan_h}:force_original_aspect_ratio=increase",
            f"crop={overscan_w}:{overscan_h}",
            (
                "zoompan="
                f"z='min(zoom+{zoom_speed:.4f},1.16)':"
                f"x='(iw-iw/zoom)*({start_x:.3f}+({delta_x:.3f})*on/{frames})':"
                f"y='(ih-ih/zoom)*({start_y:.3f}+({delta_y:.3f})*on/{frames})':"
                f"d={frames}:s={width}x{height}:fps={SHORTS_FPS}"
            ),
        ]
        if subtitle_path:
            filters.append(self._subtitle_filter(subtitle_path, width, height))
        filter_chain = ",".join(filters)

        command = [ffmpeg_path, "-y", "-loop", "1", "-i", str(image_path)]
        if audio_path:
            command.extend(["-i", str(audio_path), "-map", "0:v:0", "-map", "1:a:0"])
        else:
            command.extend(["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100", "-map", "0:v:0", "-map", "1:a:0"])

        command.extend(
            [
                "-vf",
                filter_chain,
                "-t",
                f"{target_duration:.3f}",
                "-shortest",
                "-r",
                str(SHORTS_FPS),
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "20",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "160k",
                "-movflags",
                "+faststart",
                str(output_path),
            ]
        )

        try:
            self._run(command)
        except subprocess.CalledProcessError:
            if not subtitle_path:
                raise
            self.logger.warning("Subtitle burn failed for %s. Retrying scene clip without captions.", image_path)
            return self._build_scene_clip(
                ffmpeg_path=ffmpeg_path,
                image_path=image_path,
                audio_path=audio_path,
                target_duration=target_duration,
                width=width,
                height=height,
                scene_index=scene_index,
                subtitle_path=None,
                output_path=output_path,
            )
        return output_path

    def _finalize_scene_clip(
        self,
        *,
        ffmpeg_path: str,
        visual_video_path: Path,
        audio_path: Path | None,
        target_duration: float,
        width: int,
        height: int,
        subtitle_path: Path | None,
        output_path: Path,
    ) -> Path:
        command = [ffmpeg_path, "-y", "-i", str(visual_video_path)]
        if audio_path:
            command.extend(["-i", str(audio_path), "-map", "0:v:0", "-map", "1:a:0"])
        else:
            command.extend(["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100", "-map", "0:v:0", "-map", "1:a:0"])

        filters: list[str] = []
        if subtitle_path:
            filters.append(self._subtitle_filter(subtitle_path, width, height))
        if filters:
            command.extend(["-vf", ",".join(filters)])

        command.extend(
            [
                "-t",
                f"{target_duration:.3f}",
                "-shortest",
                "-r",
                str(SHORTS_FPS),
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "20",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "160k",
                "-movflags",
                "+faststart",
                str(output_path),
            ]
        )
        try:
            self._run(command)
        except subprocess.CalledProcessError:
            if not subtitle_path:
                raise
            self.logger.warning("Subtitle burn failed for scene visual %s. Retrying without captions.", visual_video_path)
            return self._finalize_scene_clip(
                ffmpeg_path=ffmpeg_path,
                visual_video_path=visual_video_path,
                audio_path=audio_path,
                target_duration=target_duration,
                width=width,
                height=height,
                subtitle_path=None,
                output_path=output_path,
            )
        return output_path

    def _write_concat_manifest(self, clip_paths: list[Path], manifest_path: Path) -> None:
        lines = [f"file '{clip.resolve().as_posix()}'" for clip in clip_paths]
        manifest_path.write_text("\n".join(lines), encoding="utf-8")

    def _concat_scene_clips(self, ffmpeg_path: str, clip_paths: list[Path], manifest_path: Path, output_path: Path) -> Path:
        self._write_concat_manifest(clip_paths, manifest_path)
        self._run(
            [
                ffmpeg_path,
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(manifest_path),
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "20",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "160k",
                "-movflags",
                "+faststart",
                str(output_path),
            ]
        )
        return output_path

    def _render_fallback_scene_image(
        self,
        project: VideoProject,
        scene_index: int,
        size: tuple[int, int],
        file_path: Path,
        *,
        shot: SceneShot | None = None,
    ) -> Path:
        width, height = size
        scene = project.scenes[scene_index]
        image = Image.new("RGBA", (width, height), "#0B1220")
        draw = ImageDraw.Draw(image)

        for y in range(height):
            mix = y / max(1, height)
            color = (
                int(11 + (mix * 22)),
                int(18 + (mix * 34)),
                int(32 + (mix * 48)),
                255,
            )
            draw.line([(0, y), (width, y)], fill=color)

        accent_shapes = [
            ((-width * 0.10, height * 0.06, width * 0.62, height * 0.44), (234, 88, 12, 88)),
            ((width * 0.38, height * 0.10, width * 1.08, height * 0.58), (14, 165, 233, 60)),
            ((width * 0.06, height * 0.58, width * 0.82, height * 1.04), (249, 115, 22, 34)),
        ]
        for bounds, fill in accent_shapes:
            draw.ellipse(tuple(int(value) for value in bounds), fill=fill)

        panel_top = int(height * 0.10)
        panel_bottom = int(height * 0.92)
        draw.rounded_rectangle(
            (int(width * 0.07), panel_top, int(width * 0.93), panel_bottom),
            radius=max(28, width // 16),
            fill=(15, 23, 42, 208),
            outline=(251, 146, 60, 168),
            width=max(2, width // 240),
        )

        tag_font = _pick_font(max(18, width // 24))
        title_font = _pick_font(max(38, width // 12))
        body_font = _pick_font(max(22, width // 26))
        caption_font = _pick_font(max(20, width // 28))

        scene_tag = f"ESCENA {scene.scene_number:02d}"
        draw.rounded_rectangle(
            (int(width * 0.12), int(height * 0.14), int(width * 0.42), int(height * 0.19)),
            radius=max(16, width // 28),
            fill=(249, 115, 22, 228),
        )
        draw.text((int(width * 0.15), int(height * 0.149)), scene_tag, font=tag_font, fill="#F8FAFC")

        title_text = textwrap.fill(
            shot.focal_subject if shot and shot.focal_subject else (scene.scene_title or project.title or "Short visual"),
            width=max(10, width // 16),
        )
        draw.multiline_text(
            (int(width * 0.12), int(height * 0.24)),
            title_text,
            font=title_font,
            fill="#F8FAFC",
            spacing=8,
        )

        visual_text = textwrap.fill(
            shot.visual_prompt if shot and shot.visual_prompt else (
                scene.visual_prompt or scene.visual_description or scene.description or project.summary or "Escena visual"
            ),
            width=max(16, width // 15),
        )
        draw.multiline_text(
            (int(width * 0.12), int(height * 0.43)),
            visual_text,
            font=body_font,
            fill="#D6E4F0",
            spacing=8,
        )

        footer_top = int(height * 0.78)
        draw.rounded_rectangle(
            (int(width * 0.12), footer_top, int(width * 0.88), int(height * 0.88)),
            radius=max(18, width // 24),
            fill=(2, 6, 23, 196),
        )
        footer_text = textwrap.fill(self._scene_caption(project, scene_index), width=max(14, width // 16))
        draw.multiline_text(
            (int(width * 0.15), int(height * 0.805)),
            textwrap.fill(
                " | ".join(
                    part
                    for part in [
                        footer_text,
                        shot.shot_type if shot else "",
                        shot.camera_motion if shot else "",
                        shot.color_palette if shot else "",
                    ]
                    if part
                ),
                width=max(14, width // 16),
            ),
            font=caption_font,
            fill="#F8FAFC",
            spacing=6,
        )

        file_path.parent.mkdir(parents=True, exist_ok=True)
        image.convert("RGB").save(file_path, format="PNG")
        return file_path

    def _generate_ai_visuals(
        self,
        request: VideoRenderRequest,
        assets_dir: Path,
        progress_callback=None,
    ) -> dict[tuple[int, int], Path]:
        workflow_path = request.comfyui_workflow_path.strip()
        if not workflow_path:
            self.logger.info("Storyboard AI visuals unavailable: no workflow path configured.")
            return {}

        try:
            workflow_mode = detect_workflow_output_mode(workflow_path)
        except Exception as exc:
            self.logger.warning("Storyboard AI visuals unavailable: workflow validation failed: %s", exc)
            return {}

        if workflow_mode != "image":
            self.logger.info("Storyboard AI visuals skipped: workflow mode is %s, expected image.", workflow_mode)
            return {}

        client = ComfyUIClient(
            base_url=request.comfyui_base_url,
            timeout_seconds=request.request_timeout_seconds,
        )
        connected, message = client.test_connection()
        if not connected:
            self.logger.warning("Storyboard AI visuals unavailable: %s", message)
            return {}

        scene_shot_index: list[tuple[int, SceneShot]] = []
        for scene_index, scene in enumerate(request.project.scenes):
            target_duration = float(max(2, scene.duration_seconds))
            for shot in self._scene_shots(request.project, scene_index, target_duration):
                scene_shot_index.append((scene_index, shot))

        total_shots = max(1, len(scene_shot_index))
        width, height = self._dimensions_for_ratio(request.aspect_ratio)
        ai_visuals: dict[tuple[int, int], Path] = {}

        for item_index, (scene_index, shot) in enumerate(scene_shot_index):
            scene = request.project.scenes[scene_index]
            if progress_callback:
                progress_callback(
                    0.06 + ((item_index / total_shots) * 0.30),
                    f"Visual IA {item_index + 1}/{total_shots}: generando plano {shot.shot_number} de la escena {scene.scene_number}...",
                )
            try:
                asset = client.generate_scene_asset(
                    workflow_path=workflow_path,
                    prompt_text=self._scene_prompt(request.project, scene_index, request.aspect_ratio, shot),
                    negative_prompt=self._scene_negative_prompt(request, scene_index),
                    output_prefix=f"{sanitize_filename(request.project.title)}_storyboard_scene_{scene.scene_number:02d}_shot_{shot.shot_number:02d}",
                    destination_stem=assets_dir / f"scene_{scene.scene_number:02d}_shot_{shot.shot_number:02d}",
                    poll_interval_seconds=request.comfyui_poll_interval_seconds,
                    extra_replacements={
                        "__WIDTH__": width,
                        "__HEIGHT__": height,
                    },
                )
            except Exception as exc:
                self.logger.warning(
                    "Storyboard AI visual failed | scene_number=%s | shot_number=%s | error=%s",
                    scene.scene_number,
                    shot.shot_number,
                    exc,
                )
                continue
            if asset.asset_type != "image":
                self.logger.warning(
                    "Storyboard AI visual skipped | scene_number=%s | shot_number=%s | unexpected_asset_type=%s",
                    scene.scene_number,
                    shot.shot_number,
                    asset.asset_type,
                )
                continue
            ai_visuals[(scene_index, shot.shot_number)] = asset.file_path
            self.logger.info(
                "Storyboard AI visual completed | scene_number=%s | shot_number=%s | file_path=%s",
                scene.scene_number,
                shot.shot_number,
                asset.file_path,
            )

        return ai_visuals

    def render_storyboards(
        self,
        project: VideoProject,
        output_dir: str | Path,
        progress_callback=None,
        size: tuple[int, int] | None = None,
    ) -> list[Path]:
        width, height = size or self._dimensions_for_ratio("16:9")
        target_dir = Path(output_dir)
        storyboard_dir = target_dir / f"{now_stamp()}_{sanitize_filename(project.title)}_storyboard_frames"
        storyboard_dir.mkdir(parents=True, exist_ok=True)

        image_paths: list[Path] = []
        total_scenes = max(1, len(project.scenes))
        for index, _scene in enumerate(project.scenes):
            if progress_callback:
                progress_callback(
                    0.08 + ((index / total_scenes) * 0.5),
                    f"Storyboard {index + 1}/{total_scenes}: preparando visual base...",
                )
            file_path = storyboard_dir / f"scene_{project.scenes[index].scene_number:02d}.png"
            image_paths.append(self._render_fallback_scene_image(project, index, (width, height), file_path))
        return image_paths

    def build_video(
        self,
        project: VideoProject,
        output_dir: str | Path,
        image_paths: list[Path] | None = None,
        ffmpeg_path: str = "",
        *,
        aspect_ratio: str = "9:16",
        render_captions: bool = True,
        tts_backend: str = "Windows local",
        piper_executable_path: str = "",
        piper_model_path: str = "",
    ) -> Path:
        # Compatibility wrapper for older callers. Route through the narrated
        # storyboard renderer so legacy code paths cannot emit silent MP4s.
        request = VideoRenderRequest(
            project=project,
            output_dir=str(Path(output_dir).resolve()),
            provider="Storyboard local",
            aspect_ratio=aspect_ratio,
            render_captions=render_captions,
            tts_backend=tts_backend,
            ffmpeg_path=ffmpeg_path,
            piper_executable_path=piper_executable_path,
            piper_model_path=piper_model_path,
        )
        result = self.render(request, image_paths=image_paths)
        if result.file_path is None:
            raise RuntimeError("Storyboard render did not return an output file.")
        return result.file_path

    def render(
        self,
        request: VideoRenderRequest,
        progress_callback=None,
        image_paths: list[Path] | None = None,
    ) -> RenderedVideoResult:
        ffmpeg_path, ffprobe_path = self._resolve_ffmpeg_paths(request.ffmpeg_path)
        width, height = self._dimensions_for_ratio(request.aspect_ratio)

        target_dir = Path(request.output_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        session_root = target_dir / f"{now_stamp()}_{sanitize_filename(request.project.title)}_storyboard_session"
        visuals_dir = session_root / "visuals"
        audio_dir = session_root / "audio"
        subtitle_dir = session_root / "subtitles"
        clips_dir = session_root / "clips"
        shot_clips_dir = session_root / "shot_clips"
        scene_visuals_dir = session_root / "scene_visuals"
        fallback_dir = session_root / "fallback_visuals"
        for directory in [visuals_dir, audio_dir, subtitle_dir, clips_dir, shot_clips_dir, scene_visuals_dir, fallback_dir]:
            directory.mkdir(parents=True, exist_ok=True)

        total_scenes = max(1, len(request.project.scenes))
        self.logger.info(
            "Starting storyboard shorts render | title=%s | scenes=%s | aspect_ratio=%s | workflow=%s",
            request.project.title,
            total_scenes,
            request.aspect_ratio,
            request.comfyui_workflow_path,
        )

        provided_visuals: dict[int, Path] = {}
        if image_paths:
            normalized_paths = [Path(path) for path in image_paths]
            if len(normalized_paths) != len(request.project.scenes):
                raise ValueError(
                    "The number of supplied storyboard images must match the number of scenes."
                )
            provided_visuals = {index: path for index, path in enumerate(normalized_paths)}

        ai_visuals = {} if provided_visuals else self._generate_ai_visuals(
            request,
            visuals_dir,
            progress_callback=progress_callback,
        )
        clip_paths: list[Path] = []
        ai_visual_count = 0
        fallback_visual_count = 0

        for index, scene in enumerate(request.project.scenes):
            if progress_callback:
                progress_callback(
                    0.42 + ((index / total_scenes) * 0.42),
                    f"Componiendo short {index + 1}/{total_scenes}...",
                )

            try:
                audio_path = self._scene_audio(request, index, audio_dir)
            except Exception as exc:
                self.logger.warning(
                    "Scene audio failed | scene_number=%s | backend=%s | error=%s",
                    scene.scene_number,
                    request.tts_backend,
                    exc,
                )
                audio_path = None

            duration_seconds = self._media_duration(ffprobe_path, audio_path) if audio_path else float(max(2, scene.duration_seconds))
            subtitle_path: Path | None = None
            if request.render_captions:
                caption_text = self._scene_caption(request.project, index)
                if caption_text:
                    subtitle_path = self._write_scene_subtitle(
                        caption_text,
                        duration_seconds,
                        subtitle_dir / f"scene_{scene.scene_number:02d}.srt",
                    )

            if provided_visuals:
                visual_path = provided_visuals[index]
                clip_path = self._build_scene_clip(
                    ffmpeg_path=ffmpeg_path,
                    image_path=visual_path,
                    audio_path=audio_path,
                    target_duration=duration_seconds,
                    width=width,
                    height=height,
                    scene_index=index,
                    subtitle_path=subtitle_path,
                    output_path=clips_dir / f"scene_{scene.scene_number:02d}.mp4",
                )
            else:
                shot_plan = self._scene_shots(request.project, index, duration_seconds)
                shot_clip_paths: list[Path] = []
                for shot_index, shot in enumerate(shot_plan):
                    visual_path = ai_visuals.get((index, shot.shot_number))
                    if visual_path is None:
                        visual_path = self._render_fallback_scene_image(
                            request.project,
                            index,
                            (width, height),
                            fallback_dir / f"scene_{scene.scene_number:02d}_shot_{shot.shot_number:02d}.png",
                            shot=shot,
                        )
                        fallback_visual_count += 1
                    else:
                        ai_visual_count += 1
                    shot_clip_paths.append(
                        self._build_scene_clip(
                            ffmpeg_path=ffmpeg_path,
                            image_path=visual_path,
                            audio_path=None,
                            target_duration=max(0.4, shot.duration_seconds),
                            width=width,
                            height=height,
                            scene_index=index + shot_index,
                            subtitle_path=None,
                            output_path=shot_clips_dir / f"scene_{scene.scene_number:02d}_shot_{shot.shot_number:02d}.mp4",
                        )
                    )

                scene_visual_path = self._concat_scene_clips(
                    ffmpeg_path=ffmpeg_path,
                    clip_paths=shot_clip_paths,
                    manifest_path=scene_visuals_dir / f"scene_{scene.scene_number:02d}_manifest.txt",
                    output_path=scene_visuals_dir / f"scene_{scene.scene_number:02d}_visual.mp4",
                )
                clip_path = self._finalize_scene_clip(
                    ffmpeg_path=ffmpeg_path,
                    visual_video_path=scene_visual_path,
                    audio_path=audio_path,
                    target_duration=duration_seconds,
                    width=width,
                    height=height,
                    subtitle_path=subtitle_path,
                    output_path=clips_dir / f"scene_{scene.scene_number:02d}.mp4",
                )
            clip_paths.append(clip_path)

        if progress_callback:
            progress_callback(0.90, "Uniendo escenas y finalizando el short...")
        output_path = target_dir / f"{now_stamp()}_{sanitize_filename(request.project.title)}_storyboard.mp4"
        self._concat_scene_clips(
            ffmpeg_path=ffmpeg_path,
            clip_paths=clip_paths,
            manifest_path=session_root / "concat_manifest.txt",
            output_path=output_path,
        )
        if progress_callback:
            progress_callback(1.0, "Short visual completado.")
        self.logger.info(
            "Storyboard shorts render completed | output=%s | ai_visuals=%s | fallback_visuals=%s",
            output_path,
            ai_visual_count if not provided_visuals else 0,
            fallback_visual_count if not provided_visuals else 0,
        )
        return RenderedVideoResult(
            provider="Storyboard local",
            file_path=output_path,
            metadata={
                "session_root": str(session_root),
                "provided_visual_scenes": len(provided_visuals),
                "ai_visual_scenes": len(ai_visuals),
                "fallback_scenes": fallback_visual_count if not provided_visuals else 0,
            },
        )
