from __future__ import annotations

from dataclasses import dataclass

from .render_devices import (
    GPUDevice,
    RenderSelectionSummary,
    available_video_encoder_options,
    describe_render_selection,
    detect_ffmpeg_video_encoders,
    detect_gpu_devices,
    format_video_render_options,
)


@dataclass(frozen=True)
class GPUDetectionResult:
    devices: tuple[GPUDevice, ...]
    ffmpeg_encoders: tuple[str, ...]
    render_options: tuple[str, ...]
    encoder_options: tuple[str, ...]


class GPUDetector:
    def detect(self, ffmpeg_path: str = "") -> GPUDetectionResult:
        devices = tuple(detect_gpu_devices())
        encoders = tuple(detect_ffmpeg_video_encoders(ffmpeg_path))
        return GPUDetectionResult(
            devices=devices,
            ffmpeg_encoders=encoders,
            render_options=tuple(format_video_render_options([device.name for device in devices])),
            encoder_options=tuple(available_video_encoder_options(ffmpeg_path, set(encoders))),
        )

    def describe_selection(
        self,
        render_choice: str,
        *,
        ffmpeg_path: str = "",
        encoder_preference: str = "Auto",
    ) -> RenderSelectionSummary:
        result = self.detect(ffmpeg_path)
        return describe_render_selection(
            render_choice,
            list(result.devices),
            ffmpeg_path=ffmpeg_path,
            available_encoders=set(result.ffmpeg_encoders),
            encoder_preference=encoder_preference,
        )
