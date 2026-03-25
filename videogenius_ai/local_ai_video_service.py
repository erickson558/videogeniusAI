from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import subprocess
import threading
import textwrap
from pathlib import Path

from .comfyui_client import ComfyUIClient, detect_workflow_output_mode
from .logging_utils import configure_logging
from .models import GeneratedSceneAsset, RenderedVideoResult, VideoRenderRequest
from .prompt_director import build_cinematic_scene_prompt, build_scene_negative_prompt
from .render_devices import VideoEncoderPlan
from .tts_service import PiperTTSService, WindowsTTSService
from .utils import brief_requests_silent_narration, now_stamp, sanitize_filename
from .video_renderer import VideoRenderer

CREATE_NO_WINDOW = 0x08000000
LOGGER = configure_logging(__name__)

ASPECT_RATIO_DIMENSIONS = {
    "9:16": (720, 1280),
    "16:9": (1280, 720),
    "1:1": (1080, 1080),
}
AVATAR_WORKFLOW_DIMENSIONS = {
    "9:16": (384, 576),
    "16:9": (576, 384),
    "1:1": (512, 512),
}
AVATAR_WORKFLOW_FPS = 20


class LocalAIVideoWorkflowError(ValueError):
    pass


def _ffmpeg_escape_path(path: str | Path) -> str:
    text = str(Path(path).resolve()).replace("\\", "/")
    return text.replace(":", "\\:")


class LocalAIVideoService:
    def __init__(self, ffmpeg_path: str = "") -> None:
        self.logger = LOGGER
        renderer = VideoRenderer(ffmpeg_path, logger=self.logger)
        self.ffmpeg_path = renderer.ffmpeg.ffmpeg_path
        self.ffprobe_path = renderer.ffmpeg.ffprobe_path

    def _create_renderer(self, ffmpeg_path: str = "") -> VideoRenderer:
        return VideoRenderer(ffmpeg_path or self.ffmpeg_path, logger=self.logger)

    def _dimensions_for_ratio(self, aspect_ratio: str) -> tuple[int, int]:
        return ASPECT_RATIO_DIMENSIONS.get(aspect_ratio, ASPECT_RATIO_DIMENSIONS["9:16"])

    def _avatar_dimensions_for_ratio(self, aspect_ratio: str) -> tuple[int, int]:
        return AVATAR_WORKFLOW_DIMENSIONS.get(aspect_ratio, AVATAR_WORKFLOW_DIMENSIONS["9:16"])

    def _encoder_pool(self, request: VideoRenderRequest, video_renderer: VideoRenderer) -> list[VideoEncoderPlan]:
        pool = video_renderer.build_encoder_pool(
            request.video_render_device_preference,
            encoder_preference=request.video_encoder_preference,
        )
        self.logger.info(
            "Local AI encoder pool resolved | choice=%s | encoder_preference=%s | pool=%s",
            request.video_render_device_preference or "Auto",
            request.video_encoder_preference or "Auto",
            ", ".join(f"{plan.label}:{plan.encoder_name}" for plan in pool),
        )
        return pool

    def _media_duration(self, video_renderer: VideoRenderer, file_path: str | Path) -> float:
        return video_renderer.ffmpeg.media_duration(file_path)

    def _scene_prompt(self, request: VideoRenderRequest, scene_index: int) -> str:
        scene = request.project.scenes[scene_index]
        return build_cinematic_scene_prompt(
            request.project,
            scene,
            aspect_ratio=request.aspect_ratio,
        )

    def _scene_negative_prompt(self, request: VideoRenderRequest, scene_index: int) -> str:
        scene = request.project.scenes[scene_index]
        return build_scene_negative_prompt(
            request.comfyui_negative_prompt,
            scene.negative_prompt,
        )

    def _scene_caption(self, request: VideoRenderRequest, scene_index: int) -> str:
        if brief_requests_silent_narration(request.project.source_topic):
            return ""
        scene = request.project.scenes[scene_index]
        return (scene.narration or scene.description or scene.scene_title or "").strip()

    def _scene_audio(
        self,
        request: VideoRenderRequest,
        scene_index: int,
        assets_dir: Path,
    ) -> Path | None:
        if brief_requests_silent_narration(request.project.source_topic):
            return None
        scene = request.project.scenes[scene_index]
        narration = (scene.narration or scene.description or "").strip()
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

        output_path = assets_dir / f"scene_{scene.scene_number:02d}.wav"
        return tts.synthesize(narration, output_path)

    def _worker_urls(self, request: VideoRenderRequest) -> list[str]:
        seen: set[str] = set()
        urls: list[str] = []
        for raw in [request.comfyui_base_url, *request.comfyui_worker_urls.split(",")]:
            value = raw.strip().rstrip("/")
            if not value or value in seen:
                continue
            seen.add(value)
            urls.append(value)
        return urls or [request.comfyui_base_url.strip().rstrip("/")]

    def _effective_worker_count(self, request: VideoRenderRequest, total_scenes: int) -> int:
        worker_urls = self._worker_urls(request)
        return min(
            max(1, request.parallel_scene_workers),
            max(1, len(worker_urls)),
            max(1, total_scenes),
        )

    def _write_scene_subtitle(self, caption_text: str, duration_seconds: float, subtitle_path: Path) -> Path:
        subtitle_path.parent.mkdir(parents=True, exist_ok=True)
        wrapped = textwrap.fill(caption_text, width=38)
        hours = int(duration_seconds // 3600)
        minutes = int((duration_seconds % 3600) // 60)
        seconds = int(duration_seconds % 60)
        milliseconds = int((duration_seconds - int(duration_seconds)) * 1000)
        end_timestamp = f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"
        subtitle_path.write_text(
            "1\n00:00:00,000 --> " + end_timestamp + "\n" + wrapped + "\n",
            encoding="utf-8",
        )
        return subtitle_path

    def _build_scene_filter(self, width: int, height: int, subtitle_path: Path | None) -> str:
        filters = [
            f"scale={width}:{height}:force_original_aspect_ratio=increase",
            f"crop={width}:{height}",
        ]
        if subtitle_path:
            filters.append(f"subtitles='{_ffmpeg_escape_path(subtitle_path)}'")
        return ",".join(filters)

    def _build_scene_clip(
        self,
        *,
        video_renderer: VideoRenderer,
        asset: GeneratedSceneAsset,
        audio_path: Path | None,
        target_duration: float,
        width: int,
        height: int,
        subtitle_path: Path | None,
        output_path: Path,
        encoder_plan: VideoEncoderPlan,
    ) -> tuple[Path, VideoEncoderPlan]:
        filter_chain = self._build_scene_filter(width, height, subtitle_path)
        def build_command(active_plan: VideoEncoderPlan) -> list[str]:
            command = [video_renderer.ffmpeg.ffmpeg_path, "-y"]

            if asset.asset_type == "video":
                command.extend(["-stream_loop", "-1", "-i", str(asset.file_path)])
            else:
                command.extend(["-loop", "1", "-i", str(asset.file_path)])

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
                    "30",
                ]
            )
            command.extend(active_plan.ffmpeg_args)
            command.extend(
                [
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
            return command

        try:
            used_plan = video_renderer.run_with_fallback(
                build_command,
                encoder_plan,
                stage_label=f"local-ai-scene-{output_path.stem}",
            )
        except subprocess.CalledProcessError:
            if not subtitle_path:
                raise
            self.logger.warning("Subtitle burn failed for %s. Retrying scene clip without captions.", asset.file_path)
            return self._build_scene_clip(
                video_renderer=video_renderer,
                asset=asset,
                audio_path=audio_path,
                target_duration=target_duration,
                width=width,
                height=height,
                subtitle_path=None,
                output_path=output_path,
                encoder_plan=encoder_plan,
            )
        return output_path, used_plan

    def _concat_scene_clips(
        self,
        video_renderer: VideoRenderer,
        clip_paths: list[Path],
        manifest_path: Path,
        output_path: Path,
        *,
        encoder_plan: VideoEncoderPlan,
    ) -> tuple[Path, VideoEncoderPlan]:
        lines = [f"file '{clip.as_posix()}'" for clip in clip_paths]
        manifest_path.write_text("\n".join(lines), encoding="utf-8")
        def build_command(active_plan: VideoEncoderPlan) -> list[str]:
            command = [
                video_renderer.ffmpeg.ffmpeg_path,
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(manifest_path),
            ]
            command.extend(active_plan.ffmpeg_args)
            command.extend(
                [
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
            return command

        used_plan = video_renderer.run_with_fallback(
            build_command,
            encoder_plan,
            stage_label=f"local-ai-concat-{output_path.stem}",
        )
        return output_path, used_plan

    def _generate_assets(
        self,
        *,
        request: VideoRenderRequest,
        assets_dir: Path,
        progress_callback,
    ) -> dict[int, GeneratedSceneAsset]:
        worker_urls = self._worker_urls(request)
        total_scenes = max(1, len(request.project.scenes))
        max_workers = self._effective_worker_count(request, total_scenes)
        self.logger.info(
            "Generating Local AI assets | workers=%s | worker_urls=%s | gpu_preference=%s",
            max_workers,
            ", ".join(worker_urls),
            request.render_gpu_preference or "Auto",
        )

        assignments: list[list[int]] = [[] for _ in range(max_workers)]
        for index in range(total_scenes):
            assignments[index % max_workers].append(index)

        lock = threading.Lock()
        completed_assets = 0
        generated_assets: dict[int, GeneratedSceneAsset] = {}

        def run_worker(worker_index: int) -> None:
            nonlocal completed_assets
            client = ComfyUIClient(
                base_url=worker_urls[worker_index],
                timeout_seconds=request.request_timeout_seconds,
            )
            worker_scenes = assignments[worker_index]
            self.logger.info(
                "ComfyUI worker started | worker_index=%s | base_url=%s | assigned_scenes=%s",
                worker_index + 1,
                worker_urls[worker_index],
                [request.project.scenes[index].scene_number for index in worker_scenes],
            )
            for scene_index in worker_scenes:
                scene = request.project.scenes[scene_index]
                with lock:
                    current_completed = completed_assets
                if progress_callback:
                    progress_callback(
                        0.06 + (current_completed / total_scenes) * 0.44,
                        f"Worker {worker_index + 1}/{max_workers}: generando escena {scene.scene_number}/{total_scenes}...",
                    )
                asset = client.generate_scene_asset(
                    workflow_path=request.comfyui_workflow_path,
                    prompt_text=self._scene_prompt(request, scene_index),
                    negative_prompt=self._scene_negative_prompt(request, scene_index),
                    output_prefix=f"{sanitize_filename(request.project.title)}_scene_{scene.scene_number:02d}",
                    destination_stem=assets_dir / f"scene_{scene.scene_number:02d}",
                    poll_interval_seconds=request.comfyui_poll_interval_seconds,
                )
                if asset.asset_type != "video":
                    raise LocalAIVideoWorkflowError(
                        "ComfyUI devolvio una imagen estatica para una escena. "
                        "Para 'Local AI video' el workflow debe devolver clips de video o gifs animados."
                    )
                with lock:
                    generated_assets[scene_index] = asset
                    completed_assets += 1
                    self.logger.info(
                        "Scene asset completed | worker_index=%s | scene_number=%s | asset_type=%s | file_path=%s",
                        worker_index + 1,
                        scene.scene_number,
                        asset.asset_type,
                        asset.file_path,
                    )
                    if progress_callback:
                        progress_callback(
                            0.1 + (completed_assets / total_scenes) * 0.46,
                            f"Assets IA: {completed_assets}/{total_scenes} escenas listas.",
                        )

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(run_worker, worker_index) for worker_index in range(max_workers)]
            for future in futures:
                future.result()

        return generated_assets

    def _avatar_replacements(
        self,
        avatar_image_path: Path,
        audio_path: Path,
        *,
        width: int,
        height: int,
        fps: int,
        duration_seconds: float,
    ) -> dict[str, str | int]:
        image_text = str(avatar_image_path.resolve())
        audio_text = str(audio_path.resolve())
        scene_frames = max(fps * 2, min(480, int(duration_seconds * fps) + 8))
        return {
            "__SOURCE_IMAGE__": image_text,
            "__AVATAR_IMAGE__": image_text,
            "__SOURCE_AVATAR__": image_text,
            "__AUDIO_FILE__": audio_text,
            "__AUDIO_PATH__": audio_text,
            "__DRIVEN_AUDIO__": audio_text,
            "__WIDTH__": width,
            "__HEIGHT__": height,
            "__FPS__": fps,
            "__SCENE_FRAMES__": scene_frames,
        }

    def _generate_avatar_assets(
        self,
        *,
        request: VideoRenderRequest,
        avatar_image_path: Path,
        assets_dir: Path,
        audio_dir: Path,
        progress_callback,
    ) -> tuple[dict[int, GeneratedSceneAsset], dict[int, Path]]:
        if request.tts_backend == "Sin voz":
            raise LocalAIVideoWorkflowError(
                "Local Avatar video necesita audio por escena para lipsync. Usa Windows local o Piper local."
            )

        worker_urls = self._worker_urls(request)
        total_scenes = max(1, len(request.project.scenes))
        generated_assets: dict[int, GeneratedSceneAsset] = {}
        generated_audio: dict[int, Path] = {}
        self.logger.info(
            "Generating Local Avatar assets | workers=%s | worker_urls=%s | avatar_image=%s",
            len(worker_urls),
            ", ".join(worker_urls),
            avatar_image_path,
        )
        avatar_width, avatar_height = self._avatar_dimensions_for_ratio(request.aspect_ratio)

        for index, scene in enumerate(request.project.scenes):
            if progress_callback:
                progress_callback(
                    0.06 + ((index / total_scenes) * 0.42),
                    f"Avatar local: preparando escena {scene.scene_number}/{total_scenes}...",
                )
            audio_path = self._scene_audio(request, index, audio_dir)
            if audio_path is None:
                raise LocalAIVideoWorkflowError(
                    f"La escena {scene.scene_number} no tiene audio utilizable. "
                    "Local Avatar video necesita narracion o descripcion para generar lipsync."
                )
            audio_duration = self._media_duration(audio_path)
            client = ComfyUIClient(
                base_url=worker_urls[index % len(worker_urls)],
                timeout_seconds=request.request_timeout_seconds,
            )
            asset = client.generate_scene_asset(
                workflow_path=request.comfyui_workflow_path,
                prompt_text=self._scene_prompt(request, index),
                negative_prompt=self._scene_negative_prompt(request, index),
                output_prefix=f"{sanitize_filename(request.project.title)}_avatar_scene_{scene.scene_number:02d}",
                destination_stem=assets_dir / f"avatar_scene_{scene.scene_number:02d}",
                poll_interval_seconds=request.comfyui_poll_interval_seconds,
                extra_replacements=self._avatar_replacements(
                    avatar_image_path,
                    audio_path,
                    width=avatar_width,
                    height=avatar_height,
                    fps=AVATAR_WORKFLOW_FPS,
                    duration_seconds=audio_duration,
                ),
            )
            if asset.asset_type != "video":
                raise LocalAIVideoWorkflowError(
                    "El workflow de avatar devolvio una imagen estatica. "
                    "Para 'Local Avatar video' el workflow debe devolver clips de video o gifs animados."
                )
            generated_assets[index] = asset
            generated_audio[index] = audio_path
            self.logger.info(
                "Avatar scene completed | scene_number=%s | asset_type=%s | file_path=%s",
                scene.scene_number,
                asset.asset_type,
                asset.file_path,
            )
            if progress_callback:
                progress_callback(
                    0.1 + (((index + 1) / total_scenes) * 0.42),
                    f"Avatar local: {index + 1}/{total_scenes} escenas listas.",
                )
        return generated_assets, generated_audio

    def _render_avatar_video(self, request: VideoRenderRequest, progress_callback=None) -> RenderedVideoResult:
        if not request.avatar_source_image_path.strip():
            raise LocalAIVideoWorkflowError(
                "Local Avatar video necesita una imagen base del avatar."
            )
        avatar_image_path = Path(request.avatar_source_image_path).expanduser()
        if not avatar_image_path.exists():
            raise FileNotFoundError(f"Avatar source image not found: {avatar_image_path}")
        if not request.comfyui_workflow_path.strip():
            raise ValueError("Select a ComfyUI workflow JSON file before using Local Avatar video mode.")
        workflow_mode = detect_workflow_output_mode(request.comfyui_workflow_path)
        if workflow_mode == "image":
            raise LocalAIVideoWorkflowError(
                "El workflow seleccionado solo genera imagenes estaticas. "
                "Para 'Local Avatar video' necesitas un workflow que produzca videos o gifs reales."
            )

        session_root = Path(request.output_dir) / f"{now_stamp()}_{sanitize_filename(request.project.title)}_local_avatar"
        assets_dir = session_root / "assets"
        audio_dir = session_root / "audio"
        subtitle_dir = session_root / "subtitles"
        clips_dir = session_root / "clips"
        for directory in [assets_dir, audio_dir, subtitle_dir, clips_dir]:
            directory.mkdir(parents=True, exist_ok=True)

        video_renderer = self._create_renderer(request.ffmpeg_path)
        encoder_pool = self._encoder_pool(request, video_renderer)
        primary_encoder = encoder_pool[0]
        render_device_labels = list(dict.fromkeys(plan.label for plan in encoder_pool))
        width, height = self._dimensions_for_ratio(request.aspect_ratio)
        total_scenes = max(1, len(request.project.scenes))
        self.logger.info(
            "Starting Local Avatar render | title=%s | scenes=%s | avatar_image=%s | workflow=%s | render_devices=%s",
            request.project.title,
            total_scenes,
            avatar_image_path,
            request.comfyui_workflow_path,
            ", ".join(render_device_labels),
        )
        generated_assets, generated_audio = self._generate_avatar_assets(
            request=request,
            avatar_image_path=avatar_image_path,
            assets_dir=assets_dir,
            audio_dir=audio_dir,
            progress_callback=progress_callback,
        )

        scene_payloads: list[dict[str, object]] = []
        for index, scene in enumerate(request.project.scenes):
            audio_path = generated_audio[index]
            duration_seconds = self._media_duration(video_renderer, audio_path)
            subtitle_path: Path | None = None
            if request.render_captions:
                caption_text = self._scene_caption(request, index)
                if caption_text:
                    subtitle_path = self._write_scene_subtitle(
                        caption_text,
                        duration_seconds,
                        subtitle_dir / f"scene_{scene.scene_number:02d}.srt",
                    )
            scene_payloads.append(
                {
                    "index": index,
                    "scene": scene,
                    "audio_path": audio_path,
                    "duration_seconds": duration_seconds,
                    "subtitle_path": subtitle_path,
                }
            )

        def compose_scene(payload: dict[str, object]) -> tuple[int, Path, str]:
            index = int(payload["index"])
            scene = payload["scene"]
            assert hasattr(scene, "scene_number")
            audio_path = payload["audio_path"] if isinstance(payload["audio_path"], Path) else None
            duration_seconds = float(payload["duration_seconds"])
            subtitle_path = payload["subtitle_path"] if isinstance(payload["subtitle_path"], Path) else None
            encoder_plan = encoder_pool[index % len(encoder_pool)]
            clip_path, used_plan = self._build_scene_clip(
                video_renderer=video_renderer,
                asset=generated_assets[index],
                audio_path=audio_path,
                target_duration=duration_seconds,
                width=width,
                height=height,
                subtitle_path=subtitle_path,
                output_path=clips_dir / f"scene_{scene.scene_number:02d}.mp4",
                encoder_plan=encoder_plan,
            )
            return index, clip_path, used_plan.encoder_name

        clip_results: dict[int, Path] = {}
        scene_encoder_names: set[str] = set()
        if len(encoder_pool) > 1 and total_scenes > 1:
            if progress_callback:
                progress_callback(0.58, "Componiendo clips avatar en varias GPU...")
            with ThreadPoolExecutor(max_workers=min(len(encoder_pool), total_scenes)) as executor:
                futures = [executor.submit(compose_scene, payload) for payload in scene_payloads]
                completed = 0
                for future in as_completed(futures):
                    index, clip_path, encoder_name = future.result()
                    clip_results[index] = clip_path
                    scene_encoder_names.add(encoder_name)
                    completed += 1
                    if progress_callback:
                        progress_callback(
                            0.58 + ((completed / total_scenes) * 0.30),
                            f"Componiendo clip avatar {completed}/{total_scenes}...",
                        )
        else:
            for completed, payload in enumerate(scene_payloads, start=1):
                if progress_callback:
                    progress_callback(
                        0.58 + (((completed - 1) / total_scenes) * 0.30),
                        f"Componiendo clip avatar {completed}/{total_scenes}...",
                    )
                index, clip_path, encoder_name = compose_scene(payload)
                clip_results[index] = clip_path
                scene_encoder_names.add(encoder_name)

        clip_paths = [clip_results[index] for index in range(len(request.project.scenes))]

        if progress_callback:
            progress_callback(0.92, "Uniendo clips avatar y finalizando el MP4...")
        output_path = Path(request.output_dir) / f"{now_stamp()}_{sanitize_filename(request.project.title)}_local_avatar.mp4"
        output_path, final_output_plan = self._concat_scene_clips(
            video_renderer=video_renderer,
            clip_paths=clip_paths,
            manifest_path=session_root / "concat_manifest.txt",
            output_path=output_path,
            encoder_plan=primary_encoder,
        )
        scene_encoder_names.add(final_output_plan.encoder_name)
        if progress_callback:
            progress_callback(1.0, "Video avatar completado.")
        self.logger.info(
            "Local Avatar render completed | output=%s | session_root=%s",
            output_path,
            session_root,
        )
        return RenderedVideoResult(
            provider="Local Avatar video",
            file_path=output_path,
            metadata={
                "session_root": str(session_root),
                "avatar_image_path": str(avatar_image_path),
                "scenes_rendered": total_scenes,
                "video_encoder": final_output_plan.encoder_name if len(scene_encoder_names) == 1 else ", ".join(sorted(scene_encoder_names)),
                "video_encoder_requested": request.video_encoder_preference or "Auto",
                "video_render_devices": ", ".join(render_device_labels),
            },
        )

    def render(self, request: VideoRenderRequest, progress_callback=None) -> RenderedVideoResult:
        if (request.provider or "").strip() == "Local Avatar video":
            return self._render_avatar_video(request, progress_callback)
        if not request.comfyui_workflow_path.strip():
            raise ValueError("Select a ComfyUI workflow JSON file before using Local AI video mode.")
        workflow_mode = detect_workflow_output_mode(request.comfyui_workflow_path)
        if workflow_mode == "image":
            raise LocalAIVideoWorkflowError(
                "El workflow de ComfyUI seleccionado solo genera imagenes estaticas. "
                "Para 'Local AI video' necesitas un workflow que produzca videos o gifs reales por escena."
            )

        session_root = Path(request.output_dir) / f"{now_stamp()}_{sanitize_filename(request.project.title)}_local_ai"
        assets_dir = session_root / "assets"
        audio_dir = session_root / "audio"
        subtitle_dir = session_root / "subtitles"
        clips_dir = session_root / "clips"
        for directory in [assets_dir, audio_dir, subtitle_dir, clips_dir]:
            directory.mkdir(parents=True, exist_ok=True)

        video_renderer = self._create_renderer(request.ffmpeg_path)
        encoder_pool = self._encoder_pool(request, video_renderer)
        primary_encoder = encoder_pool[0]
        render_device_labels = list(dict.fromkeys(plan.label for plan in encoder_pool))
        width, height = self._dimensions_for_ratio(request.aspect_ratio)
        total_scenes = max(1, len(request.project.scenes))
        effective_workers = self._effective_worker_count(request, total_scenes)
        self.logger.info(
            "Starting Local AI render | title=%s | scenes=%s | workers=%s | aspect_ratio=%s | workflow=%s | render_devices=%s",
            request.project.title,
            total_scenes,
            effective_workers,
            request.aspect_ratio,
            request.comfyui_workflow_path,
            ", ".join(render_device_labels),
        )
        if progress_callback:
            progress_callback(0.04, "Detectando workers de ComfyUI y preparando render IA...")
        generated_assets = self._generate_assets(
            request=request,
            assets_dir=assets_dir,
            progress_callback=progress_callback,
        )

        scene_payloads: list[dict[str, object]] = []
        for index, scene in enumerate(request.project.scenes):
            audio_path = self._scene_audio(request, index, audio_dir)
            duration_seconds = self._media_duration(video_renderer, audio_path) if audio_path else float(scene.duration_seconds)
            subtitle_path: Path | None = None
            if request.render_captions:
                caption_text = self._scene_caption(request, index)
                if caption_text:
                    subtitle_path = self._write_scene_subtitle(
                        caption_text,
                        duration_seconds,
                        subtitle_dir / f"scene_{scene.scene_number:02d}.srt",
                    )
            scene_payloads.append(
                {
                    "index": index,
                    "scene": scene,
                    "audio_path": audio_path,
                    "duration_seconds": duration_seconds,
                    "subtitle_path": subtitle_path,
                }
            )

        def compose_scene(payload: dict[str, object]) -> tuple[int, Path, str]:
            index = int(payload["index"])
            scene = payload["scene"]
            assert hasattr(scene, "scene_number")
            audio_path = payload["audio_path"] if isinstance(payload["audio_path"], Path) else None
            duration_seconds = float(payload["duration_seconds"])
            subtitle_path = payload["subtitle_path"] if isinstance(payload["subtitle_path"], Path) else None
            encoder_plan = encoder_pool[index % len(encoder_pool)]
            clip_path, used_plan = self._build_scene_clip(
                video_renderer=video_renderer,
                asset=generated_assets[index],
                audio_path=audio_path,
                target_duration=duration_seconds,
                width=width,
                height=height,
                subtitle_path=subtitle_path,
                output_path=clips_dir / f"scene_{scene.scene_number:02d}.mp4",
                encoder_plan=encoder_plan,
            )
            return index, clip_path, used_plan.encoder_name

        clip_results: dict[int, Path] = {}
        scene_encoder_names: set[str] = set()
        if len(encoder_pool) > 1 and total_scenes > 1:
            if progress_callback:
                progress_callback(0.60, "Componiendo clips con varias GPU...")
            with ThreadPoolExecutor(max_workers=min(len(encoder_pool), total_scenes)) as executor:
                futures = [executor.submit(compose_scene, payload) for payload in scene_payloads]
                completed = 0
                for future in as_completed(futures):
                    index, clip_path, encoder_name = future.result()
                    clip_results[index] = clip_path
                    scene_encoder_names.add(encoder_name)
                    completed += 1
                    if progress_callback:
                        progress_callback(
                            0.60 + ((completed / total_scenes) * 0.28),
                            f"Componiendo clip {completed}/{total_scenes}...",
                        )
        else:
            for completed, payload in enumerate(scene_payloads, start=1):
                if progress_callback:
                    scene = payload["scene"]
                    assert hasattr(scene, "scene_number")
                    progress_callback(
                        0.60 + (((completed - 1) / total_scenes) * 0.28),
                        f"Componiendo clip {completed}/{total_scenes} de la escena {scene.scene_number}...",
                    )
                index, clip_path, encoder_name = compose_scene(payload)
                clip_results[index] = clip_path
                scene_encoder_names.add(encoder_name)

        clip_paths = [clip_results[index] for index in range(len(request.project.scenes))]

        if progress_callback:
            progress_callback(0.92, "Uniendo clips y finalizando el MP4...")
        output_path = Path(request.output_dir) / f"{now_stamp()}_{sanitize_filename(request.project.title)}_local_ai.mp4"
        output_path, final_output_plan = self._concat_scene_clips(
            video_renderer=video_renderer,
            clip_paths=clip_paths,
            manifest_path=session_root / "concat_manifest.txt",
            output_path=output_path,
            encoder_plan=primary_encoder,
        )
        scene_encoder_names.add(final_output_plan.encoder_name)
        if progress_callback:
            progress_callback(1.0, "Video final completado.")
        self.logger.info(
            "Local AI render completed | output=%s | session_root=%s | workers=%s",
            output_path,
            session_root,
            effective_workers,
        )
        return RenderedVideoResult(
            provider="Local AI video",
            file_path=output_path,
            metadata={
                "session_root": str(session_root),
                "workers_used": effective_workers,
                "scenes_rendered": total_scenes,
                "video_encoder": final_output_plan.encoder_name if len(scene_encoder_names) == 1 else ", ".join(sorted(scene_encoder_names)),
                "video_encoder_requested": request.video_encoder_preference or "Auto",
                "video_render_devices": ", ".join(render_device_labels),
            },
        )
