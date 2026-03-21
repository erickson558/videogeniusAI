from __future__ import annotations

from typing import Callable

from .local_ai_video_service import LocalAIVideoService
from .logging_utils import configure_logging
from .models import RenderedVideoResult, VideoRenderRequest
from .video_service import StoryboardVideoService

ProgressCallback = Callable[[float, str], None]

LOGGER = configure_logging(__name__)


class VideoRenderService:
    def __init__(self, storyboard_service: StoryboardVideoService | None = None, local_ai_service: LocalAIVideoService | None = None) -> None:
        self.logger = LOGGER
        self.storyboard_service = storyboard_service or StoryboardVideoService()
        self.local_ai_service = local_ai_service

    def _normalize_provider(self, provider: str) -> str:
        normalized = (provider or "Storyboard local").strip()
        if normalized not in {"Storyboard local", "Local AI video", "Local Avatar video"}:
            return "Storyboard local"
        return normalized

    def _render_storyboard(self, request: VideoRenderRequest, progress_callback: ProgressCallback | None) -> RenderedVideoResult:
        self.logger.info(
            "Starting storyboard render | title=%s | scenes=%s | aspect_ratio=%s | captions=%s",
            request.project.title,
            len(request.project.scenes),
            request.aspect_ratio,
            request.render_captions,
        )
        result = self.storyboard_service.render(request, progress_callback)
        self.logger.info("Storyboard render completed | output=%s", result.file_path)
        return result

    def render(self, request: VideoRenderRequest, progress_callback: ProgressCallback | None = None) -> RenderedVideoResult:
        provider = self._normalize_provider(request.provider)
        self.logger.info(
            "Dispatching video render | provider=%s | title=%s | scenes=%s",
            provider,
            request.project.title,
            len(request.project.scenes),
        )
        if provider == "Storyboard local":
            return self._render_storyboard(request, progress_callback)
        service = self.local_ai_service or LocalAIVideoService(ffmpeg_path=request.ffmpeg_path)
        return service.render(request, progress_callback)
