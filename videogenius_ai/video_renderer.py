from __future__ import annotations

import subprocess
from logging import Logger
from typing import Callable

from .ffmpeg_wrapper import FFmpegWrapper
from .gpu_detector import GPUDetector
from .logging_utils import configure_logging
from .render_devices import (
    RenderSelectionSummary,
    VideoEncoderPlan,
    build_video_encoder_pool,
)

LOGGER = configure_logging(__name__)

CommandFactory = Callable[[VideoEncoderPlan], list[str]]


class VideoRenderer:
    def __init__(
        self,
        ffmpeg_path: str = "",
        *,
        logger: Logger | None = None,
        gpu_detector: GPUDetector | None = None,
    ) -> None:
        self.logger = logger or LOGGER
        self.ffmpeg = FFmpegWrapper(ffmpeg_path)
        self.gpu_detector = gpu_detector or GPUDetector()

    def describe_selection(
        self,
        render_choice: str,
        *,
        encoder_preference: str = "Auto",
    ) -> RenderSelectionSummary:
        return self.gpu_detector.describe_selection(
            render_choice,
            ffmpeg_path=self.ffmpeg.ffmpeg_path,
            encoder_preference=encoder_preference,
        )

    def build_encoder_pool(
        self,
        render_choice: str,
        *,
        encoder_preference: str = "Auto",
    ) -> list[VideoEncoderPlan]:
        detection = self.gpu_detector.detect(self.ffmpeg.ffmpeg_path)
        return build_video_encoder_pool(
            render_choice,
            list(detection.devices),
            ffmpeg_path=self.ffmpeg.ffmpeg_path,
            available_encoders=set(detection.ffmpeg_encoders),
            encoder_preference=encoder_preference,
        )

    def run_with_fallback(
        self,
        command_factory: CommandFactory,
        encoder_plan: VideoEncoderPlan,
        *,
        stage_label: str,
        allow_cpu_fallback: bool = True,
    ) -> VideoEncoderPlan:
        command = command_factory(encoder_plan)
        try:
            self.ffmpeg.run(command)
            return encoder_plan
        except subprocess.CalledProcessError as exc:
            if not allow_cpu_fallback or not encoder_plan.hardware_accelerated:
                raise
            cpu_plan = build_video_encoder_pool(
                "CPU only",
                [],
                ffmpeg_path=self.ffmpeg.ffmpeg_path,
                encoder_preference="libx264",
            )[0]
            self.logger.warning(
                "Hardware render failed, retrying with CPU | stage=%s | encoder=%s | stderr=%s",
                stage_label,
                encoder_plan.encoder_name,
                (exc.stderr.decode("utf-8", errors="ignore") if isinstance(exc.stderr, bytes) else str(exc.stderr or "")).strip(),
            )
            self.ffmpeg.run(command_factory(cpu_plan))
            return cpu_plan
