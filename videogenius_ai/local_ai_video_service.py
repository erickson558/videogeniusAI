from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import subprocess
import threading
import textwrap
from pathlib import Path
from shutil import which

from .comfyui_client import ComfyUIClient
from .models import GeneratedSceneAsset, RenderedVideoResult, VideoRenderRequest
from .tts_service import PiperTTSService, WindowsTTSService
from .utils import now_stamp, sanitize_filename

CREATE_NO_WINDOW = 0x08000000

ASPECT_RATIO_DIMENSIONS = {
    "9:16": (720, 1280),
    "16:9": (1280, 720),
    "1:1": (1080, 1080),
}


def _ffmpeg_escape_path(path: str | Path) -> str:
    text = str(Path(path).resolve()).replace("\\", "/")
    return text.replace(":", "\\:")


class LocalAIVideoService:
    def __init__(self, ffmpeg_path: str = "") -> None:
        resolved_ffmpeg = ffmpeg_path.strip() if ffmpeg_path.strip() and Path(ffmpeg_path).exists() else (which("ffmpeg") or which("ffmpeg.exe"))
        resolved_ffprobe = ""
        if resolved_ffmpeg:
            sibling = Path(resolved_ffmpeg).with_name("ffprobe.exe")
            if sibling.exists():
                resolved_ffprobe = str(sibling.resolve())
        if not resolved_ffprobe:
            resolved_ffprobe = which("ffprobe") or which("ffprobe.exe") or ""
        if not resolved_ffmpeg or not resolved_ffprobe:
            raise FileNotFoundError("FFmpeg and FFprobe must be available in PATH.")
        self.ffmpeg_path = resolved_ffmpeg
        self.ffprobe_path = resolved_ffprobe

    def _dimensions_for_ratio(self, aspect_ratio: str) -> tuple[int, int]:
        return ASPECT_RATIO_DIMENSIONS.get(aspect_ratio, ASPECT_RATIO_DIMENSIONS["9:16"])

    def _run(self, command: list[str]) -> None:
        subprocess.run(
            command,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=CREATE_NO_WINDOW,
        )

    def _media_duration(self, file_path: str | Path) -> float:
        result = subprocess.run(
            [
                self.ffprobe_path,
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

    def _scene_prompt(self, request: VideoRenderRequest, scene_index: int) -> str:
        scene = request.project.scenes[scene_index]
        visual = scene.visual_prompt or scene.visual_description or scene.description or request.project.visual_style
        return " | ".join(
            [
                request.project.visual_style,
                request.project.video_format,
                request.project.output_language,
                f"Scene {scene.scene_number}",
                visual,
            ]
        )

    def _scene_caption(self, request: VideoRenderRequest, scene_index: int) -> str:
        scene = request.project.scenes[scene_index]
        return (scene.narration or scene.description or scene.scene_title or "").strip()

    def _scene_audio(
        self,
        request: VideoRenderRequest,
        scene_index: int,
        assets_dir: Path,
    ) -> Path | None:
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
        asset: GeneratedSceneAsset,
        audio_path: Path | None,
        target_duration: float,
        width: int,
        height: int,
        subtitle_path: Path | None,
        output_path: Path,
    ) -> Path:
        filter_chain = self._build_scene_filter(width, height, subtitle_path)
        command = [self.ffmpeg_path, "-y"]

        if asset.asset_type == "video":
            command.extend(["-stream_loop", "-1", "-i", str(asset.file_path)])
        else:
            command.extend(["-loop", "1", "-i", str(asset.file_path)])

        if audio_path:
            command.extend(["-i", str(audio_path)])
            command.extend(["-map", "0:v:0", "-map", "1:a:0"])
        else:
            command.extend(["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"])
            command.extend(["-map", "0:v:0", "-map", "1:a:0"])

        command.extend(
            [
                "-vf",
                filter_chain,
                "-t",
                f"{target_duration:.3f}",
                "-shortest",
                "-r",
                "30",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                str(output_path),
            ]
        )
        self._run(command)
        return output_path

    def _concat_scene_clips(self, clip_paths: list[Path], manifest_path: Path, output_path: Path) -> Path:
        lines = [f"file '{clip.as_posix()}'" for clip in clip_paths]
        manifest_path.write_text("\n".join(lines), encoding="utf-8")
        self._run(
            [
                self.ffmpeg_path,
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(manifest_path),
                "-c",
                "copy",
                str(output_path),
            ]
        )
        return output_path

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
                    negative_prompt=request.comfyui_negative_prompt,
                    output_prefix=f"{sanitize_filename(request.project.title)}_scene_{scene.scene_number:02d}",
                    destination_stem=assets_dir / f"scene_{scene.scene_number:02d}",
                    poll_interval_seconds=request.comfyui_poll_interval_seconds,
                )
                with lock:
                    generated_assets[scene_index] = asset
                    completed_assets += 1
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

    def render(self, request: VideoRenderRequest, progress_callback=None) -> RenderedVideoResult:
        if not request.comfyui_workflow_path.strip():
            raise ValueError("Select a ComfyUI workflow JSON file before using Local AI video mode.")

        session_root = Path(request.output_dir) / f"{now_stamp()}_{sanitize_filename(request.project.title)}_local_ai"
        assets_dir = session_root / "assets"
        audio_dir = session_root / "audio"
        subtitle_dir = session_root / "subtitles"
        clips_dir = session_root / "clips"
        for directory in [assets_dir, audio_dir, subtitle_dir, clips_dir]:
            directory.mkdir(parents=True, exist_ok=True)

        width, height = self._dimensions_for_ratio(request.aspect_ratio)
        clip_paths: list[Path] = []
        total_scenes = max(1, len(request.project.scenes))
        effective_workers = self._effective_worker_count(request, total_scenes)
        if progress_callback:
            progress_callback(0.04, "Detectando workers de ComfyUI y preparando render IA...")
        generated_assets = self._generate_assets(
            request=request,
            assets_dir=assets_dir,
            progress_callback=progress_callback,
        )

        for index, scene in enumerate(request.project.scenes):
            audio_path = self._scene_audio(request, index, audio_dir)
            duration_seconds = self._media_duration(audio_path) if audio_path else float(scene.duration_seconds)
            subtitle_path: Path | None = None
            if request.render_captions:
                caption_text = self._scene_caption(request, index)
                if caption_text:
                    subtitle_path = self._write_scene_subtitle(
                        caption_text,
                        duration_seconds,
                        subtitle_dir / f"scene_{scene.scene_number:02d}.srt",
                    )

            if progress_callback:
                progress_callback(
                    0.6 + ((index / total_scenes) * 0.28),
                    f"Componiendo clip {index + 1}/{total_scenes} de la escena {scene.scene_number}...",
                )
            clip_path = self._build_scene_clip(
                asset=generated_assets[index],
                audio_path=audio_path,
                target_duration=duration_seconds,
                width=width,
                height=height,
                subtitle_path=subtitle_path,
                output_path=clips_dir / f"scene_{scene.scene_number:02d}.mp4",
            )
            clip_paths.append(clip_path)

        if progress_callback:
            progress_callback(0.92, "Uniendo clips y finalizando el MP4...")
        output_path = Path(request.output_dir) / f"{now_stamp()}_{sanitize_filename(request.project.title)}_local_ai.mp4"
        self._concat_scene_clips(
            clip_paths=clip_paths,
            manifest_path=session_root / "concat_manifest.txt",
            output_path=output_path,
        )
        if progress_callback:
            progress_callback(1.0, "Video final completado.")
        return RenderedVideoResult(
            provider="Local AI video",
            file_path=output_path,
            metadata={
                "session_root": str(session_root),
                "workers_used": effective_workers,
                "scenes_rendered": total_scenes,
            },
        )
