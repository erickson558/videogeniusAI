from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from shutil import which
from typing import Callable
from urllib.parse import urlparse

import requests

from .comfyui_client import ComfyUIClient
from .config import AppConfig
from .lmstudio_client import LMStudioClient
from .paths import APP_ROOT, RUNTIME_DIR, WORKFLOWS_DIR
from .render_devices import detect_gpu_names, format_local_ai_gpu_options, format_video_render_options, gpu_index_from_choice

CREATE_NO_WINDOW = 0x08000000

LM_STUDIO_PACKAGE_ID = "ElementLabs.LMStudio"
COMFYUI_PACKAGE_ID = "Comfy.ComfyUI-Desktop"
FFMPEG_PACKAGE_ID = "Gyan.FFmpeg.Essentials"

PACKAGE_LABELS = {
    LM_STUDIO_PACKAGE_ID: "LM Studio",
    COMFYUI_PACKAGE_ID: "ComfyUI Desktop",
    FFMPEG_PACKAGE_ID: "FFmpeg",
}

DEFAULT_NEGATIVE_PROMPT = (
    "low quality, blurry, distorted, muddy details, washed out colors, flat lighting, "
    "watermark, logo, text overlay, subtitle box, extra fingers, deformed face, bad anatomy, "
    "cropped subject, duplicated subject, compression artifacts"
)
DEFAULT_CHECKPOINT_FILENAME = "v1-5-pruned-emaonly-fp16.safetensors"
DEFAULT_CHECKPOINT_URL = "https://huggingface.co/Comfy-Org/stable-diffusion-v1-5-archive/resolve/main/v1-5-pruned-emaonly-fp16.safetensors"
MIN_VALID_CHECKPOINT_BYTES = 512 * 1024 * 1024
DEFAULT_AVATAR_VAE_FILENAME = "sd-vae-ft-mse.safetensors"
DEFAULT_AVATAR_VAE_URL = "https://huggingface.co/stabilityai/sd-vae-ft-mse/resolve/main/diffusion_pytorch_model.safetensors"
MIN_VALID_AVATAR_VAE_BYTES = 128 * 1024 * 1024
DEFAULT_COMFYUI_HOST = "127.0.0.1"
DEFAULT_COMFYUI_PORT = 8000
AVATAR_REQUIRED_NODE_TYPES = [
    "Echo_LoadModel",
    "Echo_Predata",
    "Echo_Sampler",
    "VHS_LoadAudio",
    "VHS_LoadImagePath",
    "VHS_VideoCombine",
]
DESKTOP_SECTION_BEGIN = "# VideoGeniusAI desktop paths begin"
DESKTOP_SECTION_END = "# VideoGeniusAI desktop paths end"
MANAGED_SECTION_BEGIN = "# VideoGeniusAI managed models begin"
MANAGED_SECTION_END = "# VideoGeniusAI managed models end"

ProgressCallback = Callable[[float, str], None]


@dataclass
class SetupStatus:
    winget_available: bool
    lmstudio_installed: bool
    comfyui_installed: bool
    ffmpeg_ready: bool
    comfyui_reachable: bool
    workflow_ready: bool
    windows_tts_ready: bool
    ffmpeg_path: str = ""
    comfyui_checkpoint: str = ""
    comfyui_base_url: str = ""
    comfyui_worker_urls: list[str] = field(default_factory=list)
    model_folder: str = ""
    gpu_names: list[str] = field(default_factory=list)
    checkpoints: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def summary_lines(self) -> list[str]:
        lines = [
            f"LM Studio: {'Listo' if self.lmstudio_installed else 'Pendiente'}",
            f"ComfyUI Desktop: {'Listo' if self.comfyui_installed else 'Pendiente'}",
            f"FFmpeg: {'Listo' if self.ffmpeg_ready else 'Pendiente'}",
            f"ComfyUI API: {'Conectado' if self.comfyui_reachable else 'Sin conexion'}",
            f"Workflow automatico: {'Listo' if self.workflow_ready else 'Pendiente'}",
            f"Voz local de Windows: {'Disponible' if self.windows_tts_ready else 'No disponible'}",
        ]
        if self.gpu_names:
            lines.append(f"GPUs detectadas: {len(self.gpu_names)}")
        if self.comfyui_checkpoint:
            lines.append(f"Modelo visual: {self.comfyui_checkpoint}")
        if self.comfyui_base_url:
            lines.append(f"ComfyUI URL: {self.comfyui_base_url}")
        if self.comfyui_worker_urls:
            lines.append(f"Workers ComfyUI: {len(self.comfyui_worker_urls)}")
        if self.model_folder:
            lines.append(f"Modelos compartidos: {self.model_folder}")
        if self.notes:
            lines.extend(self.notes)
        return lines


@dataclass
class SetupPreparationResult:
    updates: dict[str, object]
    status: SetupStatus


class SetupManager:
    def __init__(self) -> None:
        self.workflows_dir = WORKFLOWS_DIR
        self.workflows_dir.mkdir(parents=True, exist_ok=True)
        self.runtime_models_dir = RUNTIME_DIR / "comfyui_models"
        self.runtime_models_dir.mkdir(parents=True, exist_ok=True)

    def _default_programs_dir(self) -> Path:
        return Path(os.environ.get("LOCALAPPDATA", "")).expanduser() / "Programs"

    def _default_comfyui_install_root(self) -> Path:
        return self._default_programs_dir() / "ComfyUI"

    def _default_lmstudio_install_root(self) -> Path:
        return Path(os.environ.get("ProgramFiles", "")).expanduser() / "LM Studio"

    def _known_application_candidates(self, app_name: str) -> list[Path]:
        normalized = app_name.strip().lower()
        if normalized == "comfyui":
            roots = [self._default_comfyui_install_root()]
            filenames = ["ComfyUI.exe", "ComfyUI Desktop.exe"]
        elif normalized == "lmstudio":
            roots = [self._default_lmstudio_install_root(), self._default_programs_dir() / "LM Studio"]
            filenames = ["LM Studio.exe", "LMStudio.exe"]
        else:
            return []
        return [root / filename for root in roots for filename in filenames]

    def _candidate_comfyui_urls(self, configured_url: str) -> list[str]:
        candidates = [
            configured_url.strip(),
            "http://127.0.0.1:8000",
            "http://127.0.0.1:8188",
            "http://127.0.0.1:8189",
            "http://127.0.0.1:8190",
            "http://127.0.0.1:8191",
        ]
        seen: set[str] = set()
        normalized: list[str] = []
        for item in candidates:
            value = item.strip().rstrip("/")
            if not value or value in seen:
                continue
            seen.add(value)
            normalized.append(value)
        return normalized

    def _run(self, command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            check=check,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            creationflags=CREATE_NO_WINDOW,
        )

    def winget_available(self) -> bool:
        return which("winget") is not None

    def _package_installed(self, package_id: str) -> bool:
        if not self.winget_available():
            return False
        result = self._run(
            [
                "winget",
                "list",
                "--id",
                package_id,
                "--exact",
                "--source",
                "winget",
                "--accept-source-agreements",
                "--disable-interactivity",
            ],
            check=False,
        )
        output = f"{result.stdout}\n{result.stderr}".lower()
        return package_id.lower() in output

    def install_package(self, package_id: str) -> str:
        if not self.winget_available():
            raise FileNotFoundError("winget is not available on this Windows installation.")
        result = self._run(
            [
                "winget",
                "install",
                "--id",
                package_id,
                "--exact",
                "--source",
                "winget",
                "--accept-source-agreements",
                "--accept-package-agreements",
                "--disable-interactivity",
                "--silent",
            ]
        )
        return (result.stdout or result.stderr or PACKAGE_LABELS.get(package_id, package_id)).strip()

    def ensure_package_installed(self, package_id: str, *, install_missing: bool = True) -> bool:
        if self._package_installed(package_id):
            return True
        if install_missing and self.winget_available():
            self.install_package(package_id)
        return self._package_installed(package_id)

    def _search_for_executable(self, filename: str, roots: list[Path]) -> str:
        for root in roots:
            if not root.exists():
                continue
            try:
                for match in root.rglob(filename):
                    if match.is_file():
                        return str(match.resolve())
            except OSError:
                continue
        return ""

    def _common_program_roots(self) -> list[Path]:
        candidates = [
            APP_ROOT,
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages",
            Path(os.environ.get("ProgramFiles", "")),
            Path(os.environ.get("ProgramFiles(x86)", "")),
        ]
        return [path for path in candidates if str(path).strip()]

    def resolve_ffmpeg_path(self, configured_path: str = "") -> str:
        candidate = configured_path.strip()
        if candidate and Path(candidate).exists():
            return str(Path(candidate).resolve())
        located = which("ffmpeg") or which("ffmpeg.exe")
        if located:
            return located
        return self._search_for_executable("ffmpeg.exe", self._common_program_roots())

    def resolve_ffprobe_path(self, ffmpeg_path: str = "") -> str:
        ffmpeg_executable = self.resolve_ffmpeg_path(ffmpeg_path)
        if ffmpeg_executable:
            sibling = Path(ffmpeg_executable).with_name("ffprobe.exe")
            if sibling.exists():
                return str(sibling.resolve())
        located = which("ffprobe") or which("ffprobe.exe")
        if located:
            return located
        return self._search_for_executable("ffprobe.exe", self._common_program_roots())

    def find_application_path(self, app_name: str) -> str:
        for candidate in self._known_application_candidates(app_name):
            if candidate.exists():
                return str(candidate.resolve())

        lookup = {
            "lmstudio": ["LM Studio.exe", "LMStudio.exe"],
            "comfyui": ["ComfyUI.exe", "ComfyUI Desktop.exe"],
        }
        roots = [path for path in self._common_program_roots() if path != self._default_programs_dir()]
        for filename in lookup.get(app_name.lower(), []):
            match = self._search_for_executable(filename, roots)
            if match:
                return match
        return ""

    def format_gpu_options(self, gpu_names: list[str]) -> list[str]:
        return format_local_ai_gpu_options(gpu_names)

    def format_video_render_options(self, gpu_names: list[str]) -> list[str]:
        return format_video_render_options(gpu_names)

    def gpu_index_from_choice(self, choice: str) -> int | None:
        return gpu_index_from_choice(choice)

    def _comfyui_launch_env(self, gpu_choice: str) -> dict[str, str]:
        env = os.environ.copy()
        gpu_index = self.gpu_index_from_choice(gpu_choice)
        if gpu_index is not None:
            env["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
            env["CUDA_VISIBLE_DEVICES"] = str(gpu_index)
            env["HIP_VISIBLE_DEVICES"] = str(gpu_index)
            env["ROCR_VISIBLE_DEVICES"] = str(gpu_index)
        return env

    def _load_comfyui_desktop_config(self) -> dict[str, object]:
        path = self.comfyui_user_config_dir() / "config.json"
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def resolve_comfyui_data_root(self) -> Path:
        payload = self._load_comfyui_desktop_config()
        configured_root = str(payload.get("basePath") or "").strip()
        if configured_root:
            return Path(configured_root).expanduser().resolve()
        return (Path.home() / "Documents" / "ComfyUI").resolve()

    def resolve_comfyui_main_script(self) -> str:
        executable = self.find_application_path("comfyui")
        candidates: list[Path] = [
            self._default_comfyui_install_root() / "resources" / "ComfyUI" / "main.py",
        ]
        if executable:
            install_root = Path(executable).resolve().parent
            candidates.insert(0, install_root / "resources" / "ComfyUI" / "main.py")
        for candidate in candidates:
            if candidate.exists():
                return str(candidate.resolve())
        return ""

    def resolve_comfyui_models_dir(self) -> Path:
        main_script = self.resolve_comfyui_main_script()
        if main_script:
            return Path(main_script).resolve().parent / "models"
        return self.resolve_comfyui_data_root() / "models"

    def resolve_comfyui_python_path(self) -> str:
        data_root = self.resolve_comfyui_data_root()
        executable = self.find_application_path("comfyui")
        candidates = [
            data_root / ".venv" / "Scripts" / "python.exe",
            self._default_comfyui_install_root() / "resources" / "python" / "python.exe",
            self._default_comfyui_install_root() / "resources" / "python_embeded" / "python.exe",
            self._default_comfyui_install_root() / "resources" / "ComfyUI" / ".venv" / "Scripts" / "python.exe",
        ]
        if executable:
            install_root = Path(executable).resolve().parent
            candidates.extend(
                [
                    install_root / "resources" / "python" / "python.exe",
                    install_root / "resources" / "python_embeded" / "python.exe",
                    install_root / "resources" / "ComfyUI" / ".venv" / "Scripts" / "python.exe",
                ]
            )
        for candidate in candidates:
            if candidate.exists():
                return str(candidate.resolve())
        return ""

    def resolve_comfyui_extra_paths_config(self) -> str:
        path = self.extra_models_config_path()
        return str(path.resolve()) if path.exists() else ""

    def default_avatar_vae_path(self) -> Path:
        target = self.resolve_comfyui_models_dir() / "vae"
        target.mkdir(parents=True, exist_ok=True)
        return target / DEFAULT_AVATAR_VAE_FILENAME

    def download_default_avatar_vae(self, progress_callback: ProgressCallback | None = None) -> Path:
        destination = self.default_avatar_vae_path()
        if destination.exists() and destination.stat().st_size >= MIN_VALID_AVATAR_VAE_BYTES:
            if progress_callback:
                progress_callback(1.0, "El VAE base para avatar ya estaba descargado.")
            return destination

        temp_path = destination.with_suffix(".download")
        if temp_path.exists():
            temp_path.unlink()

        with requests.get(DEFAULT_AVATAR_VAE_URL, stream=True, timeout=60) as response:
            response.raise_for_status()
            total = int(response.headers.get("Content-Length", "0") or "0")
            written = 0
            with temp_path.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    handle.write(chunk)
                    written += len(chunk)
                    if progress_callback and total > 0:
                        ratio = min(1.0, written / total)
                        progress_callback(ratio, f"Descargando VAE base para avatar {ratio * 100:.0f}%...")

        if temp_path.stat().st_size < MIN_VALID_AVATAR_VAE_BYTES:
            temp_path.unlink(missing_ok=True)
            raise ValueError("La descarga del VAE base para avatar parece incompleta o invalida.")

        temp_path.replace(destination)
        if progress_callback:
            progress_callback(1.0, "VAE base para avatar descargado correctamente.")
        return destination

    def _comfyui_host_port(self, configured_url: str) -> tuple[str, int]:
        parsed = urlparse(configured_url.strip() or f"http://{DEFAULT_COMFYUI_HOST}:{DEFAULT_COMFYUI_PORT}")
        host = (parsed.hostname or DEFAULT_COMFYUI_HOST).strip() or DEFAULT_COMFYUI_HOST
        port = parsed.port or DEFAULT_COMFYUI_PORT
        return host, port

    def launch_comfyui_api_server(self, configured_url: str = "", *, gpu_choice: str = "Auto") -> bool:
        resolved_url = self.resolve_comfyui_base_url(configured_url)
        if resolved_url and self._comfyui_reachable(resolved_url):
            return True

        python_executable = self.resolve_comfyui_python_path()
        main_script = self.resolve_comfyui_main_script()
        if not python_executable or not main_script:
            return False

        data_root = self.resolve_comfyui_data_root()
        output_dir = data_root / "output"
        input_dir = data_root / "input"
        user_dir = data_root / "user"
        for directory in (output_dir, input_dir, user_dir):
            directory.mkdir(parents=True, exist_ok=True)

        host, port = self._comfyui_host_port(configured_url)
        command = [
            python_executable,
            main_script,
            "--listen",
            host,
            "--port",
            str(port),
            "--output-directory",
            str(output_dir),
            "--input-directory",
            str(input_dir),
            "--user-directory",
            str(user_dir),
        ]
        extra_paths_config = self.resolve_comfyui_extra_paths_config()
        if extra_paths_config:
            command.extend(["--extra-model-paths-config", extra_paths_config])

        try:
            subprocess.Popen(
                command,
                env=self._comfyui_launch_env(gpu_choice),
                creationflags=CREATE_NO_WINDOW,
            )
            return True
        except OSError:
            return False

    def launch_application(self, app_name: str, *, gpu_choice: str = "Auto", configured_url: str = "") -> bool:
        if app_name.lower() == "comfyui" and self.launch_comfyui_api_server(configured_url, gpu_choice=gpu_choice):
            return True
        executable = self.find_application_path(app_name)
        if not executable:
            return False
        try:
            gpu_index = self.gpu_index_from_choice(gpu_choice) if app_name.lower() == "comfyui" else None
            if gpu_index is None:
                os.startfile(executable)  # type: ignore[attr-defined]
            else:
                subprocess.Popen(
                    [executable],
                    env=self._comfyui_launch_env(gpu_choice),
                    creationflags=CREATE_NO_WINDOW,
                )
            return True
        except OSError:
            return False

    def ensure_ffmpeg_ready(self, configured_path: str = "", *, install_missing: bool = True) -> str:
        ffmpeg_path = self.resolve_ffmpeg_path(configured_path)
        ffprobe_path = self.resolve_ffprobe_path(ffmpeg_path)
        if ffmpeg_path and ffprobe_path:
            return ffmpeg_path
        if install_missing:
            self.ensure_package_installed(FFMPEG_PACKAGE_ID, install_missing=True)
        ffmpeg_path = self.resolve_ffmpeg_path(configured_path)
        ffprobe_path = self.resolve_ffprobe_path(ffmpeg_path)
        return ffmpeg_path if ffmpeg_path and ffprobe_path else ""

    def detect_gpu_names(self) -> list[str]:
        return detect_gpu_names()

    def _workflow_dimensions(self, aspect_ratio: str) -> tuple[int, int]:
        mapping = {
            "9:16": (768, 1344),
            "16:9": (1344, 768),
            "1:1": (1024, 1024),
        }
        return mapping.get(aspect_ratio, mapping["9:16"])

    def _avatar_workflow_dimensions(self, aspect_ratio: str) -> tuple[int, int]:
        mapping = {
            "9:16": (384, 576),
            "16:9": (576, 384),
            "1:1": (512, 512),
        }
        return mapping.get(aspect_ratio, mapping["9:16"])

    def managed_checkpoints_dir(self) -> Path:
        path = self.runtime_models_dir / "checkpoints"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def comfyui_user_config_dir(self) -> Path:
        appdata = Path(os.environ.get("APPDATA", "")).expanduser()
        path = appdata / "ComfyUI"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def extra_models_config_path(self) -> Path:
        return self.comfyui_user_config_dir() / "extra_models_config.yaml"

    def ensure_extra_models_config(self) -> Path:
        target = self.extra_models_config_path()
        desktop_root = self.resolve_comfyui_data_root().resolve().as_posix()
        desktop_block = "\n".join(
            [
                DESKTOP_SECTION_BEGIN,
                "comfyui_desktop:",
                '  is_default: "true"',
                "  custom_nodes: custom_nodes",
                "  download_model_base: models",
                f"  base_path: {desktop_root}",
                DESKTOP_SECTION_END,
            ]
        )
        managed_root = self.runtime_models_dir.resolve().as_posix()
        managed_block = "\n".join(
            [
                MANAGED_SECTION_BEGIN,
                "videogeniusai:",
                f"  base_path: {managed_root}",
                "  checkpoints: checkpoints",
                MANAGED_SECTION_END,
            ]
        )

        if target.exists():
            current = target.read_text(encoding="utf-8")
        else:
            current = ""

        updated = current
        if DESKTOP_SECTION_BEGIN in updated and DESKTOP_SECTION_END in updated:
            before, _sep, remainder = updated.partition(DESKTOP_SECTION_BEGIN)
            _managed, _sep2, after = remainder.partition(DESKTOP_SECTION_END)
            updated = (before.rstrip() + "\n" + desktop_block + "\n" + after.lstrip()).strip() + "\n"
        elif updated.strip():
            updated = updated.rstrip() + "\n\n" + desktop_block + "\n"
        else:
            updated = desktop_block + "\n"

        if MANAGED_SECTION_BEGIN in updated and MANAGED_SECTION_END in updated:
            before, _sep, remainder = updated.partition(MANAGED_SECTION_BEGIN)
            _managed, _sep2, after = remainder.partition(MANAGED_SECTION_END)
            updated = (before.rstrip() + "\n" + managed_block + "\n" + after.lstrip()).strip() + "\n"
        else:
            updated = updated.rstrip() + "\n\n" + managed_block + "\n"

        target.write_text(updated, encoding="utf-8")
        return target

    def local_checkpoints(self) -> list[str]:
        names = []
        for suffix in ("*.safetensors", "*.ckpt", "*.pt"):
            for path in sorted(self.managed_checkpoints_dir().glob(suffix)):
                if path.is_file():
                    names.append(path.name)
        return names

    def default_checkpoint_path(self) -> Path:
        return self.managed_checkpoints_dir() / DEFAULT_CHECKPOINT_FILENAME

    def open_models_folder(self) -> Path:
        target = self.managed_checkpoints_dir()
        os.startfile(target)  # type: ignore[attr-defined]
        return target

    def download_default_checkpoint(self, progress_callback: ProgressCallback | None = None) -> Path:
        destination = self.default_checkpoint_path()
        if destination.exists() and destination.stat().st_size >= MIN_VALID_CHECKPOINT_BYTES:
            if progress_callback:
                progress_callback(1.0, "El modelo base ya estaba descargado.")
            return destination

        temp_path = destination.with_suffix(".download")
        if temp_path.exists():
            temp_path.unlink()

        with requests.get(DEFAULT_CHECKPOINT_URL, stream=True, timeout=60) as response:
            response.raise_for_status()
            total = int(response.headers.get("Content-Length", "0") or "0")
            written = 0
            with temp_path.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    handle.write(chunk)
                    written += len(chunk)
                    if progress_callback and total > 0:
                        ratio = min(1.0, written / total)
                        progress_callback(ratio, f"Descargando modelo base {ratio * 100:.0f}%...")

        if temp_path.stat().st_size < MIN_VALID_CHECKPOINT_BYTES:
            temp_path.unlink(missing_ok=True)
            raise ValueError("La descarga del modelo base parece incompleta o invalida.")

        temp_path.replace(destination)
        if progress_callback:
            progress_callback(1.0, "Modelo base descargado correctamente.")
        return destination

    def build_default_workflow_payload(self, *, checkpoint_name: str, aspect_ratio: str) -> dict[str, object]:
        width, height = self._workflow_dimensions(aspect_ratio)
        return {
            "3": {
                "inputs": {
                    "seed": "__SEED__",
                    "steps": 24,
                    "cfg": 7.0,
                    "sampler_name": "euler",
                    "scheduler": "normal",
                    "denoise": 1,
                    "model": ["4", 0],
                    "positive": ["6", 0],
                    "negative": ["7", 0],
                    "latent_image": ["5", 0],
                },
                "class_type": "KSampler",
            },
            "4": {
                "inputs": {
                    "ckpt_name": checkpoint_name,
                },
                "class_type": "CheckpointLoaderSimple",
            },
            "5": {
                "inputs": {
                    "width": width,
                    "height": height,
                    "batch_size": 1,
                },
                "class_type": "EmptyLatentImage",
            },
            "6": {
                "inputs": {
                    "text": "__PROMPT__",
                    "clip": ["4", 1],
                },
                "class_type": "CLIPTextEncode",
            },
            "7": {
                "inputs": {
                    "text": "__NEGATIVE_PROMPT__",
                    "clip": ["4", 1],
                },
                "class_type": "CLIPTextEncode",
            },
            "8": {
                "inputs": {
                    "samples": ["3", 0],
                    "vae": ["4", 2],
                },
                "class_type": "VAEDecode",
            },
            "9": {
                "inputs": {
                    "filename_prefix": "__OUTPUT_PREFIX__",
                    "images": ["8", 0],
                },
                "class_type": "SaveImage",
            },
        }

    def ensure_default_workflow(self, *, checkpoint_name: str, aspect_ratio: str) -> Path:
        if not checkpoint_name.strip():
            raise ValueError("A ComfyUI checkpoint is required to create the automatic workflow.")
        workflow_path = self.workflows_dir / "default_local_ai_workflow.json"
        payload = self.build_default_workflow_payload(checkpoint_name=checkpoint_name.strip(), aspect_ratio=aspect_ratio)
        workflow_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return workflow_path

    def build_default_avatar_workflow_payload(self, *, aspect_ratio: str) -> dict[str, object]:
        width, height = self._avatar_workflow_dimensions(aspect_ratio)
        return {
            "1": {
                "inputs": {
                "vae": DEFAULT_AVATAR_VAE_FILENAME,
                "lora": "None",
                "denoising": True,
                "infer_mode": "audio_drived",
                "lowvram": False,
                "teacache_offload": True,
                "block_offload": True,
                "use_mmgp": "None",
                "version": "V1",
            },
                "class_type": "Echo_LoadModel",
            },
            "2": {
                "inputs": {
                    "image": "__AVATAR_IMAGE__",
                    "custom_width": 0,
                    "custom_height": 0,
                },
                "class_type": "VHS_LoadImagePath",
            },
            "3": {
                "inputs": {
                    "audio_file": "__AUDIO_FILE__",
                    "seek_seconds": 0,
                    "duration": 0,
                },
                "class_type": "VHS_LoadAudio",
            },
            "4": {
                "inputs": {
                    "info": ["1", 1],
                    "image": ["2", 0],
                    "audio": ["3", 0],
                    "prompt": "__PROMPT__",
                    "negative_prompt": "__NEGATIVE_PROMPT__",
                    "pose_dir": "pose_01",
                    "width": "__WIDTH__",
                    "height": "__HEIGHT__",
                    "fps": "__FPS__",
                    "facemask_ratio": 0.1,
                    "facecrop_ratio": 0.8,
                    "length": "__SCENE_FRAMES__",
                    "partial_video_length": 65,
                    "draw_mouse": False,
                    "motion_sync_": False,
                },
                "class_type": "Echo_Predata",
            },
            "5": {
                "inputs": {
                    "model": ["1", 0],
                    "emb": ["4", 0],
                    "seed": "__SEED__",
                    "cfg": 2.5,
                    "steps": 4,
                    "sample_rate": 16000,
                    "context_frames": 8,
                    "context_overlap": 2,
                    "save_video": False,
                },
                "class_type": "Echo_Sampler",
            },
            "6": {
                "inputs": {
                    "images": ["5", 0],
                    "audio": ["3", 0],
                    "frame_rate": ["5", 1],
                    "loop_count": 0,
                    "filename_prefix": "__OUTPUT_PREFIX__",
                    "format": "video/h264-mp4",
                    "pix_fmt": "yuv420p",
                    "crf": 19,
                    "save_metadata": True,
                    "trim_to_audio": True,
                    "pingpong": False,
                    "save_output": True,
                },
                "class_type": "VHS_VideoCombine",
            },
        }

    def ensure_default_avatar_workflow(self, *, aspect_ratio: str) -> Path:
        workflow_path = self.workflows_dir / "default_local_avatar_workflow.json"
        payload = self.build_default_avatar_workflow_payload(aspect_ratio=aspect_ratio)
        workflow_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return workflow_path

    def comfyui_has_nodes(self, base_url: str, required_node_types: list[str]) -> tuple[bool, list[str]]:
        if not base_url.strip():
            return False, list(required_node_types)
        try:
            client = ComfyUIClient(base_url=base_url)
            return client.has_nodes(required_node_types)
        except Exception:
            return False, list(required_node_types)

    def _load_checkpoints(self, base_url: str) -> list[str]:
        if not base_url.strip():
            return []
        try:
            client = ComfyUIClient(base_url=base_url)
            return client.list_checkpoints()
        except Exception:
            return []

    def _comfyui_reachable(self, base_url: str) -> bool:
        if not base_url.strip():
            return False
        try:
            client = ComfyUIClient(base_url=base_url)
            success, _message = client.test_connection()
            return success
        except Exception:
            return False

    def wait_for_lmstudio(
        self,
        base_url: str,
        *,
        timeout_seconds: int = 90,
        poll_interval_seconds: int = 2,
    ) -> tuple[bool, list[str], str]:
        timeout = max(5, int(timeout_seconds))
        interval = max(1.0, float(poll_interval_seconds))
        deadline = time.monotonic() + timeout
        client = LMStudioClient(base_url=base_url, timeout_seconds=min(20, timeout))
        last_message = "LM Studio is not ready yet."

        while time.monotonic() < deadline:
            try:
                success, models, message = client.test_connection()
            except Exception as exc:
                success, models, message = False, [], str(exc)
            last_message = message
            if success:
                return True, models, message
            time.sleep(interval)

        return False, [], last_message

    def wait_for_comfyui(
        self,
        configured_url: str,
        *,
        timeout_seconds: int = 120,
        poll_interval_seconds: int = 2,
        require_checkpoints: bool = False,
    ) -> tuple[bool, str, list[str], str]:
        timeout = max(5, int(timeout_seconds))
        interval = max(1.0, float(poll_interval_seconds))
        deadline = time.monotonic() + timeout
        last_url = configured_url.strip().rstrip("/")
        last_message = "ComfyUI is not ready yet."

        while time.monotonic() < deadline:
            resolved_url = self.resolve_comfyui_base_url(last_url or configured_url)
            if resolved_url:
                last_url = resolved_url
            if last_url and self._comfyui_reachable(last_url):
                checkpoints = self._load_checkpoints(last_url)
                if not require_checkpoints or checkpoints:
                    return True, last_url, checkpoints, "Connected successfully to ComfyUI."
                last_message = "ComfyUI responded, but no checkpoints were detected yet."
            time.sleep(interval)

        return False, last_url, [], last_message

    def resolve_comfyui_base_url(self, configured_url: str) -> str:
        for candidate in self._candidate_comfyui_urls(configured_url):
            if self._comfyui_reachable(candidate):
                return candidate
        return configured_url.strip()

    def _normalize_url_list(self, value: str) -> list[str]:
        seen: set[str] = set()
        items: list[str] = []
        for part in value.split(","):
            item = part.strip().rstrip("/")
            if not item or item in seen:
                continue
            seen.add(item)
            items.append(item)
        return items

    def resolve_comfyui_worker_urls(self, configured_urls: str, base_url: str) -> list[str]:
        candidates = self._normalize_url_list(configured_urls)
        candidates.extend(self._candidate_comfyui_urls(base_url))
        seen: set[str] = set()
        resolved: list[str] = []
        for item in candidates:
            value = item.strip().rstrip("/")
            if not value or value in seen:
                continue
            seen.add(value)
            if self._comfyui_reachable(value):
                resolved.append(value)
        return resolved

    def inspect_environment(self, config: AppConfig) -> SetupStatus:
        notes: list[str] = []
        self.ensure_extra_models_config()
        gpu_names = self.detect_gpu_names()
        ffmpeg_path = self.resolve_ffmpeg_path(config.ffmpeg_path)
        workflow_ready = Path(config.comfyui_workflow_path).exists() if config.comfyui_workflow_path.strip() else False
        resolved_comfyui_url = self.resolve_comfyui_base_url(config.comfyui_base_url)
        resolved_worker_urls = self.resolve_comfyui_worker_urls(config.comfyui_worker_urls, resolved_comfyui_url)
        checkpoints = self._load_checkpoints(resolved_comfyui_url)
        local_checkpoints = self.local_checkpoints()
        comfyui_reachable = self._comfyui_reachable(resolved_comfyui_url)
        checkpoint = config.comfyui_checkpoint.strip()
        if not checkpoint and checkpoints:
            checkpoint = checkpoints[0]
        if not checkpoint and local_checkpoints:
            checkpoint = local_checkpoints[0]
        if not workflow_ready and checkpoint:
            notes.append("Pulsa 'Preparar entorno automatico' para crear el workflow inicial.")
        if not comfyui_reachable:
            notes.append("Abre ComfyUI y confirma el puerto correcto para detectar el modelo visual automaticamente.")
        else:
            notes.append(f"ComfyUI detectado en {resolved_comfyui_url}.")
        if comfyui_reachable and not checkpoints:
            notes.append("ComfyUI esta abierto, pero no hay ningun checkpoint visual detectado todavia.")
        if len(gpu_names) >= 2:
            notes.append("LM Studio puede aprovechar varias GPU desde sus propios controles de carga.")
            if len(resolved_worker_urls) <= 1:
                notes.append("Para render IA en varias GPU con ComfyUI, inicia una instancia por GPU en puertos distintos; la app repartira escenas entre todos los workers detectados.")
        elif gpu_names:
            notes.append("Se detecto una sola GPU activa para aceleracion local.")
        if gpu_names:
            notes.append(f"VideoGeniusAI detecto estas GPU: {', '.join(gpu_names)}.")
        if local_checkpoints:
            notes.append("Ya existe al menos un checkpoint en la carpeta compartida de VideoGeniusAI.")
        else:
            notes.append("Si quieres, usa 'Instalar modelo base recomendado' para automatizar tambien el checkpoint.")
        return SetupStatus(
            winget_available=self.winget_available(),
            lmstudio_installed=self._package_installed(LM_STUDIO_PACKAGE_ID),
            comfyui_installed=self._package_installed(COMFYUI_PACKAGE_ID),
            ffmpeg_ready=bool(ffmpeg_path and self.resolve_ffprobe_path(ffmpeg_path)),
            comfyui_reachable=comfyui_reachable,
            workflow_ready=workflow_ready,
            windows_tts_ready=sys.platform.startswith("win"),
            ffmpeg_path=ffmpeg_path,
            comfyui_checkpoint=checkpoint,
            comfyui_base_url=resolved_comfyui_url,
            comfyui_worker_urls=resolved_worker_urls,
            model_folder=str(self.managed_checkpoints_dir()),
            gpu_names=gpu_names,
            checkpoints=checkpoints or local_checkpoints,
            notes=notes,
        )

    def prepare_environment(
        self,
        config: AppConfig,
        *,
        install_missing: bool = True,
        install_default_checkpoint: bool = True,
        progress_callback: ProgressCallback | None = None,
    ) -> SetupPreparationResult:
        notes: list[str] = []
        if progress_callback:
            progress_callback(0.05, "Validando entorno local...")
        if install_missing and self.winget_available():
            for package_id in [LM_STUDIO_PACKAGE_ID, COMFYUI_PACKAGE_ID, FFMPEG_PACKAGE_ID]:
                if not self._package_installed(package_id):
                    if progress_callback:
                        progress_callback(0.12, f"Instalando {PACKAGE_LABELS.get(package_id, package_id)}...")
                    self.install_package(package_id)
                    notes.append(f"{PACKAGE_LABELS.get(package_id, package_id)} instalado correctamente.")

        self.ensure_extra_models_config()
        if progress_callback:
            progress_callback(0.25, "Configurando carpeta compartida de modelos para ComfyUI...")

        gpu_names = self.detect_gpu_names()
        ffmpeg_path = self.resolve_ffmpeg_path(config.ffmpeg_path)
        ffprobe_path = self.resolve_ffprobe_path(ffmpeg_path)
        resolved_comfyui_url = self.resolve_comfyui_base_url(config.comfyui_base_url)
        resolved_worker_urls = self.resolve_comfyui_worker_urls(config.comfyui_worker_urls, resolved_comfyui_url)
        checkpoints = self._load_checkpoints(resolved_comfyui_url)
        local_checkpoints = self.local_checkpoints()
        comfyui_reachable = self._comfyui_reachable(resolved_comfyui_url)
        checkpoint = config.comfyui_checkpoint.strip()
        if not checkpoint and checkpoints:
            checkpoint = checkpoints[0]
        if not checkpoint and local_checkpoints:
            checkpoint = local_checkpoints[0]

        if not checkpoint and install_default_checkpoint:
            if progress_callback:
                progress_callback(0.3, "Descargando modelo base recomendado para ComfyUI...")

            def relay(download_ratio: float, message: str) -> None:
                if progress_callback:
                    progress_callback(0.3 + (download_ratio * 0.45), message)

            downloaded = self.download_default_checkpoint(progress_callback=relay)
            checkpoint = downloaded.name
            local_checkpoints = self.local_checkpoints()
            notes.append("Se descargo el modelo base recomendado en la carpeta compartida de VideoGeniusAI.")

        if progress_callback:
            progress_callback(0.8, "Generando workflow automatico...")

        workflow_path = config.comfyui_workflow_path.strip()
        if checkpoint:
            created_workflow = self.ensure_default_workflow(
                checkpoint_name=checkpoint,
                aspect_ratio=config.video_aspect_ratio,
            )
            workflow_path = str(created_workflow)
            if not config.comfyui_negative_prompt.strip():
                notes.append(
                    "Se preparo un workflow automatico de imagen estatica para ComfyUI. "
                    "Para video IA real debes cargar un workflow que produzca videos o gifs."
                )
        elif not checkpoints and not local_checkpoints:
            if comfyui_reachable:
                notes.append("ComfyUI respondio, pero no se detecto ningun modelo visual. Instala o copia un checkpoint y reinicia ComfyUI.")
            else:
                notes.append("No se pudo conectar con ComfyUI. Abre ComfyUI y pulsa preparar de nuevo.")
        if checkpoint and comfyui_reachable and checkpoint not in checkpoints:
            notes.append("El checkpoint ya esta descargado, pero ComfyUI necesita reiniciarse para detectarlo por API.")
        if progress_callback:
            progress_callback(0.95, "Finalizando configuracion automatica...")

        status = SetupStatus(
            winget_available=self.winget_available(),
            lmstudio_installed=self._package_installed(LM_STUDIO_PACKAGE_ID),
            comfyui_installed=self._package_installed(COMFYUI_PACKAGE_ID),
            ffmpeg_ready=bool(ffmpeg_path and ffprobe_path),
            comfyui_reachable=comfyui_reachable,
            workflow_ready=bool(workflow_path and Path(workflow_path).exists()),
            windows_tts_ready=sys.platform.startswith("win"),
            ffmpeg_path=ffmpeg_path,
            comfyui_checkpoint=checkpoint,
            comfyui_base_url=resolved_comfyui_url,
            comfyui_worker_urls=resolved_worker_urls,
            model_folder=str(self.managed_checkpoints_dir()),
            gpu_names=gpu_names,
            checkpoints=checkpoints or local_checkpoints,
            notes=notes,
        )

        updates: dict[str, object] = {
            "ffmpeg_path": ffmpeg_path,
            "tts_backend": "Windows local",
            "setup_completed": status.ffmpeg_ready and status.workflow_ready,
            "comfyui_base_url": resolved_comfyui_url or config.comfyui_base_url,
            "comfyui_worker_urls": ", ".join(resolved_worker_urls),
            "parallel_scene_workers": max(1, len(resolved_worker_urls)),
        }
        if checkpoint:
            updates["comfyui_checkpoint"] = checkpoint
        if workflow_path:
            updates["comfyui_workflow_path"] = workflow_path
        if not config.comfyui_negative_prompt.strip():
            updates["comfyui_negative_prompt"] = DEFAULT_NEGATIVE_PROMPT
        return SetupPreparationResult(updates=updates, status=status)
