from __future__ import annotations

from typing import Callable

from .local_ai_video_service import LocalAIVideoService
from .logging_utils import configure_logging
from .models import RenderedVideoResult, VideoRenderRequest
from .video_service import StoryboardVideoService

ProgressCallback = Callable[[float, str], None]

LOGGER = configure_logging()


class VideoRenderService:
    def __init__(self, storyboard_service: StoryboardVideoService | None = None, local_ai_service: LocalAIVideoService | None = None) -> None:
        self.logger = LOGGER
        self.storyboard_service = storyboard_service or StoryboardVideoService()
        self.local_ai_service = local_ai_service

    def _normalize_provider(self, provider: str) -> str:
        normalized = (provider or "Storyboard local").strip()
        if normalized not in {"Storyboard local", "Local AI video"}:
            return "Storyboard local"
        return normalized

    def _render_storyboard(self, request: VideoRenderRequest, progress_callback: ProgressCallback | None) -> RenderedVideoResult:
        if progress_callback:
            progress_callback(0.2, "Rendering storyboard frames...")
        image_paths = self.storyboard_service.render_storyboards(
            request.project,
            request.output_dir,
            progress_callback=progress_callback,
        )
        if progress_callback:
            progress_callback(0.72, "Building MP4 with FFmpeg...")
        file_path = self.storyboard_service.build_video(
            request.project,
            request.output_dir,
            image_paths=image_paths,
            ffmpeg_path=request.ffmpeg_path,
        )
        if progress_callback:
            progress_callback(1.0, "Storyboard MP4 completado.")
        return RenderedVideoResult(provider="Storyboard local", file_path=file_path)

    def render(self, request: VideoRenderRequest, progress_callback: ProgressCallback | None = None) -> RenderedVideoResult:
        provider = self._normalize_provider(request.provider)
        if provider == "Storyboard local":
            return self._render_storyboard(request, progress_callback)
        service = self.local_ai_service or LocalAIVideoService(ffmpeg_path=request.ffmpeg_path)
        return service.render(request, progress_callback)
