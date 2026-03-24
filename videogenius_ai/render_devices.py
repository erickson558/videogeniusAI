from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from shutil import which

CREATE_NO_WINDOW = 0x08000000

RENDER_DEVICE_AUTO = "Auto"
RENDER_DEVICE_CPU = "CPU only"
RENDER_DEVICE_ALL = "All GPUs (split scenes)"
RENDER_DEVICE_NVIDIA = "NVIDIA (auto)"
RENDER_DEVICE_AMD = "AMD (auto)"
RENDER_DEVICE_INTEL = "Intel (auto)"

VIDEO_ENCODER_AUTO = "Auto"
VIDEO_ENCODER_CPU = "libx264"
VIDEO_ENCODER_NVENC_H264 = "h264_nvenc"
VIDEO_ENCODER_NVENC_HEVC = "hevc_nvenc"
VIDEO_ENCODER_AMF_H264 = "h264_amf"
VIDEO_ENCODER_QSV_H264 = "h264_qsv"

PREFERRED_ENCODER_ORDER = (
    VIDEO_ENCODER_NVENC_H264,
    VIDEO_ENCODER_NVENC_HEVC,
    VIDEO_ENCODER_AMF_H264,
    VIDEO_ENCODER_QSV_H264,
    VIDEO_ENCODER_CPU,
)


@dataclass(frozen=True)
class GPUDevice:
    index: int
    name: str
    vendor: str = "unknown"

    @property
    def label(self) -> str:
        return format_gpu_label(self.index, self.name)


@dataclass(frozen=True)
class VideoEncoderPlan:
    label: str
    encoder_name: str
    ffmpeg_args: tuple[str, ...]
    vendor: str = "cpu"
    gpu_index: int | None = None
    hardware_accelerated: bool = False


@dataclass(frozen=True)
class RenderSelectionSummary:
    render_choice: str
    encoder_preference: str
    selected_plan: VideoEncoderPlan
    detected_devices: tuple[GPUDevice, ...]
    available_encoders: tuple[str, ...]


def format_gpu_label(index: int, name: str) -> str:
    return f"GPU {index}: {name}"


def vendor_display_name(vendor: str) -> str:
    mapping = {
        "nvidia": "NVIDIA",
        "amd": "AMD",
        "intel": "Intel",
        "cpu": "CPU",
        "unknown": "Unknown",
    }
    return mapping.get((vendor or "unknown").strip().lower(), (vendor or "Unknown").strip() or "Unknown")


def gpu_index_from_choice(choice: str) -> int | None:
    text = (choice or "").strip()
    if not text or text.lower() == RENDER_DEVICE_AUTO.lower():
        return None
    if not text.lower().startswith("gpu "):
        return None
    number_text = text[4:].split(":", 1)[0].strip()
    try:
        return int(number_text)
    except ValueError:
        return None


def is_cpu_choice(choice: str) -> bool:
    return (choice or "").strip().lower() == RENDER_DEVICE_CPU.lower()


def is_all_gpu_choice(choice: str) -> bool:
    return (choice or "").strip().lower() == RENDER_DEVICE_ALL.lower()


def vendor_from_choice(choice: str) -> str:
    normalized = (choice or "").strip().lower()
    if normalized == RENDER_DEVICE_NVIDIA.lower():
        return "nvidia"
    if normalized == RENDER_DEVICE_AMD.lower():
        return "amd"
    if normalized == RENDER_DEVICE_INTEL.lower():
        return "intel"
    return ""


def format_local_ai_gpu_options(gpu_names: list[str]) -> list[str]:
    return [RENDER_DEVICE_AUTO, *[format_gpu_label(index, name) for index, name in enumerate(gpu_names)]]


def format_video_render_options(gpu_names: list[str]) -> list[str]:
    options = [RENDER_DEVICE_AUTO, RENDER_DEVICE_CPU]
    vendors = {_vendor_from_text(name) for name in gpu_names}
    if "nvidia" in vendors:
        options.append(RENDER_DEVICE_NVIDIA)
    if "amd" in vendors:
        options.append(RENDER_DEVICE_AMD)
    if "intel" in vendors:
        options.append(RENDER_DEVICE_INTEL)
    options.extend(format_gpu_label(index, name) for index, name in enumerate(gpu_names))
    if len(gpu_names) >= 2:
        options.append(RENDER_DEVICE_ALL)
    return options


def available_video_encoder_options(ffmpeg_path: str = "", available_encoders: set[str] | None = None) -> list[str]:
    encoders = available_encoders if available_encoders is not None else set(detect_ffmpeg_video_encoders(ffmpeg_path))
    ordered = [VIDEO_ENCODER_AUTO]
    for encoder_name in PREFERRED_ENCODER_ORDER:
        if encoder_name == VIDEO_ENCODER_CPU or encoder_name in encoders:
            ordered.append(encoder_name)
    return ordered


def detect_gpu_devices() -> list[GPUDevice]:
    devices: list[GPUDevice] = []
    if sys.platform.startswith("win"):
        try:
            result = _run(
                [
                    "powershell",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    (
                        "(Get-CimInstance Win32_VideoController | "
                        "Select-Object Name,AdapterCompatibility | ConvertTo-Json -Compress)"
                    ),
                ]
            )
            raw = result.stdout.strip()
            if raw:
                payload = json.loads(raw)
                items = payload if isinstance(payload, list) else [payload]
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get("Name") or "").strip()
                    adapter = str(item.get("AdapterCompatibility") or "").strip()
                    if not name or _is_software_adapter(name):
                        continue
                    devices.append(
                        GPUDevice(
                            index=len(devices),
                            name=name,
                            vendor=_vendor_from_text(name, adapter),
                        )
                    )
        except Exception:
            devices = []

    if devices:
        return devices

    nvidia_smi = which("nvidia-smi") or which("nvidia-smi.exe")
    if not nvidia_smi:
        return []
    try:
        result = _run(
            [
                nvidia_smi,
                "--query-gpu=name",
                "--format=csv,noheader",
            ]
        )
        return [
            GPUDevice(index=index, name=line.strip(), vendor="nvidia")
            for index, line in enumerate(result.stdout.splitlines())
            if line.strip()
        ]
    except Exception:
        return []


def detect_gpu_names() -> list[str]:
    return [device.name for device in detect_gpu_devices()]


def describe_render_selection(
    choice: str,
    gpu_devices: list[GPUDevice],
    *,
    ffmpeg_path: str = "",
    available_encoders: set[str] | None = None,
    encoder_preference: str = VIDEO_ENCODER_AUTO,
) -> RenderSelectionSummary:
    encoders = available_encoders if available_encoders is not None else set(detect_ffmpeg_video_encoders(ffmpeg_path))
    selected_plan = build_video_encoder_pool(
        choice,
        gpu_devices,
        ffmpeg_path=ffmpeg_path,
        available_encoders=encoders,
        encoder_preference=encoder_preference,
    )[0]
    return RenderSelectionSummary(
        render_choice=(choice or "").strip() or RENDER_DEVICE_AUTO,
        encoder_preference=_normalize_encoder_preference(encoder_preference),
        selected_plan=selected_plan,
        detected_devices=tuple(gpu_devices),
        available_encoders=tuple(sorted(encoders)),
    )


def build_video_encoder_pool(
    choice: str,
    gpu_devices: list[GPUDevice],
    *,
    ffmpeg_path: str = "",
    available_encoders: set[str] | None = None,
    encoder_preference: str = VIDEO_ENCODER_AUTO,
) -> list[VideoEncoderPlan]:
    encoders = available_encoders if available_encoders is not None else set(detect_ffmpeg_video_encoders(ffmpeg_path))
    normalized_choice = (choice or "").strip() or RENDER_DEVICE_AUTO
    normalized_encoder = _normalize_encoder_preference(encoder_preference)

    if is_cpu_choice(normalized_choice) or normalized_encoder == VIDEO_ENCODER_CPU:
        return [_cpu_encoder_plan()]

    if not gpu_devices:
        return [_cpu_encoder_plan()]

    if is_all_gpu_choice(normalized_choice):
        plans = [
            _build_encoder_plan_for_device(
                device,
                encoders,
                encoder_preference=normalized_encoder,
            )
            for device in gpu_devices
        ]
        hardware_plans = [plan for plan in plans if plan.hardware_accelerated]
        return hardware_plans or [_cpu_encoder_plan()]

    selected_index = gpu_index_from_choice(normalized_choice)
    if selected_index is not None:
        for device in gpu_devices:
            if device.index == selected_index:
                return [
                    _build_encoder_plan_for_device(
                        device,
                        encoders,
                        encoder_preference=normalized_encoder,
                    )
                ]
        return [_cpu_encoder_plan()]

    selected_vendor = vendor_from_choice(normalized_choice)
    if selected_vendor:
        vendor_devices = [device for device in gpu_devices if device.vendor == selected_vendor]
        if not vendor_devices:
            return [_cpu_encoder_plan()]
        return [
            _build_encoder_plan_for_device(
                vendor_devices[0],
                encoders,
                encoder_preference=normalized_encoder,
            )
        ]

    for device in _devices_in_preferred_order(gpu_devices, normalized_encoder):
        plan = _build_encoder_plan_for_device(
            device,
            encoders,
            encoder_preference=normalized_encoder,
        )
        if plan.hardware_accelerated:
            return [plan]
    return [_cpu_encoder_plan()]


@lru_cache(maxsize=8)
def detect_ffmpeg_video_encoders(ffmpeg_path: str = "") -> tuple[str, ...]:
    resolved_ffmpeg = _resolve_ffmpeg_path(ffmpeg_path)
    if not resolved_ffmpeg:
        return ()
    try:
        result = _run([resolved_ffmpeg, "-hide_banner", "-encoders"], text=True)
    except Exception:
        return ()

    encoders: set[str] = set()
    for line in result.stdout.splitlines():
        text = line.strip()
        if not text or text.startswith("Encoders:") or text.startswith("--"):
            continue
        parts = text.split()
        if len(parts) < 2:
            continue
        flags = parts[0]
        if flags and flags[0] == "V":
            encoders.add(parts[1].strip())
    return tuple(sorted(encoders))


def _normalize_encoder_preference(value: str) -> str:
    normalized = (value or VIDEO_ENCODER_AUTO).strip().lower()
    valid = {
        VIDEO_ENCODER_AUTO.lower(): VIDEO_ENCODER_AUTO,
        VIDEO_ENCODER_CPU.lower(): VIDEO_ENCODER_CPU,
        VIDEO_ENCODER_NVENC_H264.lower(): VIDEO_ENCODER_NVENC_H264,
        VIDEO_ENCODER_NVENC_HEVC.lower(): VIDEO_ENCODER_NVENC_HEVC,
        VIDEO_ENCODER_AMF_H264.lower(): VIDEO_ENCODER_AMF_H264,
        VIDEO_ENCODER_QSV_H264.lower(): VIDEO_ENCODER_QSV_H264,
    }
    return valid.get(normalized, VIDEO_ENCODER_AUTO)


def _devices_in_preferred_order(gpu_devices: list[GPUDevice], encoder_preference: str) -> list[GPUDevice]:
    preferred_vendor = _required_vendor_for_encoder(encoder_preference)
    if preferred_vendor:
        matching = [device for device in gpu_devices if device.vendor == preferred_vendor]
        rest = [device for device in gpu_devices if device.vendor != preferred_vendor]
        return [*matching, *rest]
    return list(gpu_devices)


def _required_vendor_for_encoder(encoder_name: str) -> str:
    normalized = _normalize_encoder_preference(encoder_name)
    if normalized in {VIDEO_ENCODER_NVENC_H264, VIDEO_ENCODER_NVENC_HEVC}:
        return "nvidia"
    if normalized == VIDEO_ENCODER_AMF_H264:
        return "amd"
    if normalized == VIDEO_ENCODER_QSV_H264:
        return "intel"
    return ""


def _cpu_encoder_plan(label: str = RENDER_DEVICE_CPU) -> VideoEncoderPlan:
    return VideoEncoderPlan(
        label=label,
        encoder_name=VIDEO_ENCODER_CPU,
        ffmpeg_args=(
            "-c:v",
            VIDEO_ENCODER_CPU,
            "-preset",
            "medium",
            "-crf",
            "20",
        ),
        vendor="cpu",
        hardware_accelerated=False,
    )


def _build_encoder_plan_for_device(
    device: GPUDevice,
    available_encoders: set[str],
    *,
    encoder_preference: str = VIDEO_ENCODER_AUTO,
) -> VideoEncoderPlan:
    preferred_encoder = _normalize_encoder_preference(encoder_preference)

    if device.vendor == "nvidia":
        if preferred_encoder in {VIDEO_ENCODER_AUTO, VIDEO_ENCODER_NVENC_H264} and VIDEO_ENCODER_NVENC_H264 in available_encoders:
            return VideoEncoderPlan(
                label=device.label,
                encoder_name=VIDEO_ENCODER_NVENC_H264,
                ffmpeg_args=(
                    "-c:v",
                    VIDEO_ENCODER_NVENC_H264,
                    "-preset",
                    "p4",
                    "-cq",
                    "20",
                    "-b:v",
                    "0",
                    "-gpu",
                    str(device.index),
                ),
                vendor=device.vendor,
                gpu_index=device.index,
                hardware_accelerated=True,
            )
        if preferred_encoder in {VIDEO_ENCODER_AUTO, VIDEO_ENCODER_NVENC_HEVC} and VIDEO_ENCODER_NVENC_HEVC in available_encoders:
            return VideoEncoderPlan(
                label=device.label,
                encoder_name=VIDEO_ENCODER_NVENC_HEVC,
                ffmpeg_args=(
                    "-c:v",
                    VIDEO_ENCODER_NVENC_HEVC,
                    "-preset",
                    "p4",
                    "-cq",
                    "24",
                    "-b:v",
                    "0",
                    "-gpu",
                    str(device.index),
                ),
                vendor=device.vendor,
                gpu_index=device.index,
                hardware_accelerated=True,
            )
    if device.vendor == "intel" and preferred_encoder in {VIDEO_ENCODER_AUTO, VIDEO_ENCODER_QSV_H264} and VIDEO_ENCODER_QSV_H264 in available_encoders:
        return VideoEncoderPlan(
            label=device.label,
            encoder_name=VIDEO_ENCODER_QSV_H264,
            ffmpeg_args=(
                "-c:v",
                VIDEO_ENCODER_QSV_H264,
                "-global_quality",
                "23",
            ),
            vendor=device.vendor,
            gpu_index=device.index,
            hardware_accelerated=True,
        )
    if device.vendor == "amd" and preferred_encoder in {VIDEO_ENCODER_AUTO, VIDEO_ENCODER_AMF_H264} and VIDEO_ENCODER_AMF_H264 in available_encoders:
        return VideoEncoderPlan(
            label=device.label,
            encoder_name=VIDEO_ENCODER_AMF_H264,
            ffmpeg_args=(
                "-c:v",
                VIDEO_ENCODER_AMF_H264,
                "-usage",
                "transcoding",
                "-quality",
                "speed",
                "-rc",
                "cqp",
                "-qp_i",
                "20",
                "-qp_p",
                "22",
            ),
            vendor=device.vendor,
            gpu_index=device.index,
            hardware_accelerated=True,
        )

    fallback_label = f"{device.label} -> CPU fallback"
    return _cpu_encoder_plan(fallback_label)


def _resolve_ffmpeg_path(ffmpeg_path: str) -> str:
    configured = ffmpeg_path.strip()
    if configured and Path(configured).exists():
        return str(Path(configured).resolve())
    return which("ffmpeg") or which("ffmpeg.exe") or ""


def _run(command: list[str], *, text: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=CREATE_NO_WINDOW,
        text=text,
    )


def _is_software_adapter(name: str) -> bool:
    lowered = name.casefold()
    return any(
        marker in lowered
        for marker in [
            "microsoft basic render",
            "microsoft remote display",
            "citrix indirect display",
            "rdpudd chained dd",
            "virtualbox graphics adapter",
            "vmware svga",
        ]
    )


def _vendor_from_text(*parts: str) -> str:
    text = " ".join(part for part in parts if part).casefold()
    if any(token in text for token in ["nvidia", "geforce", "quadro", "tesla", "rtx", "gtx"]):
        return "nvidia"
    if any(token in text for token in ["intel", "arc", "iris", "uhd", "xe"]):
        return "intel"
    if any(token in text for token in ["amd", "radeon", "advanced micro devices"]):
        return "amd"
    return "unknown"
