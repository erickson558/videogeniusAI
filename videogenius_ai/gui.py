from __future__ import annotations

from dataclasses import dataclass, replace
import json
import os
import queue
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog
from typing import Any, Callable

import customtkinter as ctk
import requests

from .comfyui_client import ComfyUIClient, detect_workflow_output_mode
from .config import AppConfig, ConfigManager, sanitize_window_geometry
from .export_service import ExportService
from .generator_service import SceneGeneratorService
from .gpu_detector import GPUDetectionResult, GPUDetector
from .history_service import HistoryEntry, HistoryService
from .i18n import SUPPORTED_UI_LANGUAGES, TranslationManager, ui_language_code_from_label, ui_language_label
from .local_ai_video_service import LocalAIVideoWorkflowError
from .lmstudio_client import LMStudioClient
from .logging_utils import configure_logging
from .models import GenerationRequest, VideoProject, VideoRenderRequest
from .paths import APP_ROOT
from .prompt_director import summarize_scene_shots
from .render_devices import describe_render_selection
from .setup_manager import COMFYUI_PACKAGE_ID, LM_STUDIO_PACKAGE_ID, SetupManager, SetupStatus
from .utils import aspect_ratio_for_video_format, brief_requests_silent_narration
from .version import APP_NAME, DISPLAY_VERSION
from .video_render_service import VideoRenderService


def ui_color(light: str, dark: str) -> tuple[str, str]:
    return (light, dark)


# Central palette tokens keep the custom look consistent across the entire UI.
THEME = {
    "app_bg": ui_color("#E6ECF4", "#050816"),
    "main_panel": ui_color("#F8FAFC", "#0B1120"),
    "sidebar": ui_color("#102542", "#08101F"),
    "status_bar": ui_color("#0F172A", "#020617"),
    "hero": ui_color("#08101F", "#111827"),
    "hero_text": ui_color("#F8FAFC", "#F8FAFC"),
    "accent": ui_color("#38BDF8", "#67E8F9"),
    "muted_text": ui_color("#475569", "#94A3B8"),
    "soft_text": ui_color("#CBD5E1", "#CBD5E1"),
    "primary_text": ui_color("#0F172A", "#E2E8F0"),
    "input_bg": ui_color("#EDF2F7", "#111827"),
    "input_border": ui_color("#D97706", "#F59E0B"),
    "surface": ui_color("#FFFFFF", "#0F172A"),
    "surface_alt": ui_color("#F8FAFC", "#111827"),
    "surface_border": ui_color("#E2E8F0", "#1F2937"),
    "card": ui_color("#0B1628", "#101827"),
    "card_label": ui_color("#E2E8F0", "#CBD5E1"),
    "status_default": ui_color("#E2E8F0", "#E2E8F0"),
    "status_error": ui_color("#FCA5A5", "#FCA5A5"),
    "status_success": ui_color("#86EFAC", "#86EFAC"),
    "history_button": ui_color("#E2E8F0", "#1F2937"),
    "history_hover": ui_color("#CBD5E1", "#334155"),
    "menu_bg": ui_color("#0F172A", "#111827"),
    "progress_bg": ui_color("#1E293B", "#1E293B"),
}

# Default theme bootstrap happens before the root window is created.
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


@dataclass
class UIStateSnapshot:
    # Background jobs use this snapshot to push a full UI refresh through the queue safely.
    topic_text: str = ""
    setup_summary: str = ""
    status_text: str = ""
    status_color: Any = None
    connection_chip: str = ""
    render_chip: str = ""
    progress_value: float = 0.0
    progress_percent: str = ""
    progress_detail: str = ""
    countdown_text: str = ""


class HoverToolTip:
    def __init__(self, widget: tk.Widget, text_provider: Callable[[], str]) -> None:
        # Tooltips are created lazily so the UI stays lightweight until the user hovers.
        self.widget = widget
        self.text_provider = text_provider
        self.tooltip_window: tk.Toplevel | None = None
        self._show_job: str | None = None
        widget.bind("<Enter>", self._schedule_show, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule_show(self, _event: tk.Event | None = None) -> None:
        self._cancel_scheduled_show()
        self._show_job = self.widget.after(320, self._show)

    def _cancel_scheduled_show(self) -> None:
        if self._show_job:
            try:
                self.widget.after_cancel(self._show_job)
            except tk.TclError:
                pass
            self._show_job = None

    def _show(self) -> None:
        self._show_job = None
        text = self.text_provider().strip()
        if not text:
            return
        self._hide()
        try:
            x_pos = self.widget.winfo_rootx() + 18
            y_pos = self.widget.winfo_rooty() + self.widget.winfo_height() + 10
            tooltip = tk.Toplevel(self.widget)
            tooltip.wm_overrideredirect(True)
            tooltip.attributes("-topmost", True)
            tooltip.configure(bg="#0F172A")
            label = tk.Label(
                tooltip,
                text=text,
                justify="left",
                wraplength=320,
                padx=10,
                pady=8,
                bg="#0F172A",
                fg="#E2E8F0",
                relief="solid",
                borderwidth=1,
                font=("Segoe UI", 9),
            )
            label.pack()
            tooltip.geometry(f"+{x_pos}+{y_pos}")
            self.tooltip_window = tooltip
        except tk.TclError:
            self.tooltip_window = None

    def _hide(self, _event: tk.Event | None = None) -> None:
        self._cancel_scheduled_show()
        if self.tooltip_window is not None:
            try:
                self.tooltip_window.destroy()
            except tk.TclError:
                pass
            self.tooltip_window = None


class VideoGeniusApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        # Keep the window withdrawn during startup instead of using alpha-based transparency.
        # On Windows, layered alpha windows can repaint poorly while scrolling or dragging.
        self._startup_hidden = False
        try:
            self.withdraw()
            self._startup_hidden = True
        except tk.TclError:
            self._startup_hidden = False
        self.logger = configure_logging(__name__)
        self.config_manager = ConfigManager()
        self.app_config: AppConfig = self.config_manager.config
        self.translator = TranslationManager(self.app_config.ui_language)
        ctk.set_appearance_mode(self._normalize_appearance_mode(self.app_config.appearance_mode))
        # Core services are wired before building widgets so the UI can bind directly to them.
        self.setup_manager = SetupManager()
        self.gpu_detector = GPUDetector()
        self.generator_service = SceneGeneratorService()
        self.history_service = HistoryService()
        self.export_service = ExportService()
        self.video_render_service = VideoRenderService()

        self.current_project: VideoProject | None = None
        self.current_history_path: Path | None = None
        self.task_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self.is_busy = False
        self._closing = False
        self._save_job_id: str | None = None
        self._geometry_job_id: str | None = None
        self._countdown_job_id: str | None = None
        self._process_queue_job_id: str | None = None
        self._startup_show_job_id: str | None = None
        self._startup_front_job_id: str | None = None
        self._topmost_reset_job_id: str | None = None
        self._inspect_env_job_id: str | None = None
        self._load_models_job_id: str | None = None
        self._auto_start_job_id: str | None = None
        self._zoom_job_id: str | None = None
        self._auto_close_trigger_job_id: str | None = None
        self._render_capabilities_job_id: str | None = None
        self._render_summary_job_id: str | None = None
        self._auto_close_remaining = max(1, int(self.app_config.auto_close_seconds))
        self._last_gpu_detection: GPUDetectionResult | None = None
        self._tooltips: list[HoverToolTip] = []

        # Build the window structure first, then hydrate it with persisted config and background jobs.
        self._configure_root()
        self._create_variables()
        self._build_menu()
        self._build_layout()
        self._populate_detected_gpu_options()
        self._sync_video_provider_ui()
        self._sync_tts_ui()
        self._bind_shortcuts()
        self._bind_activity_reset()
        self._load_history_buttons()
        self._set_status(self.t("app.ready"))
        self._schedule_initial_window_show()
        # Long-running environment checks are deferred so the first paint stays responsive.
        self._process_queue_job_id = self.after(150, self._process_task_queue)
        self._countdown_job_id = self.after(1000, self._tick_auto_close)
        self._inspect_env_job_id = self.after(2200, self.inspect_environment)
        self._load_models_job_id = self.after(900, self._load_models_background)
        if self.app_config.auto_start_enabled:
            self._auto_start_job_id = self.after(1200, self.start_generation)

    def _configure_root(self) -> None:
        self.title(f"{APP_NAME} {DISPLAY_VERSION}")
        self.geometry(self._geometry_for_current_screen(self.app_config.window_geometry))
        self.minsize(1320, 840)
        self.configure(fg_color=THEME["app_bg"])
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.report_callback_exception = self._report_callback_exception
        icon_path = APP_ROOT / f"{APP_NAME.lower()}.ico"
        if icon_path.exists():
            try:
                self.iconbitmap(str(icon_path))
            except tk.TclError:
                self.logger.warning("Unable to load application icon from %s", icon_path)
        if self.app_config.window_zoomed:
            self._zoom_job_id = self.after(300, lambda: self.state("zoomed"))

    def _geometry_for_current_screen(self, geometry: str) -> str:
        safe_geometry = sanitize_window_geometry(geometry)
        try:
            size_part, x_part, y_part = safe_geometry.replace("+", " +").replace("-", " -").split()
            width_text, height_text = size_part.split("x", 1)
            width = int(width_text)
            height = int(height_text)
            x_pos = int(x_part)
            y_pos = int(y_part)
            screen_width = max(width, self.winfo_screenwidth())
            screen_height = max(height, self.winfo_screenheight())
            min_visible_x = 120 - width
            max_x = max(0, screen_width - 120)
            min_visible_y = 0
            max_y = max(0, screen_height - 80)
            clamped_x = min(max(x_pos, min_visible_x), max_x)
            clamped_y = min(max(y_pos, min_visible_y), max_y)
            return f"{width}x{height}{clamped_x:+d}{clamped_y:+d}"
        except (ValueError, tk.TclError):
            return safe_geometry

    def _schedule_initial_window_show(self) -> None:
        self._startup_show_job_id = self.after(0, self._finalize_initial_window)
        self._startup_front_job_id = self.after(120, self._bring_window_to_front)

    def _bring_window_to_front(self) -> None:
        try:
            self.lift()
            self.attributes("-topmost", True)
            self.focus_force()
            self._topmost_reset_job_id = self.after(350, lambda: self.attributes("-topmost", False))
        except tk.TclError:
            pass

    def _finalize_initial_window(self) -> None:
        self.update_idletasks()
        if not self.app_config.window_zoomed:
            self.geometry(self._geometry_for_current_screen(self.app_config.window_geometry))
        if self._startup_hidden:
            try:
                self.deiconify()
                self._startup_hidden = False
            except tk.TclError:
                pass
        if self.app_config.window_zoomed:
            self.state("zoomed")
        self.title(f"{APP_NAME} {DISPLAY_VERSION}")

    def _normalize_appearance_mode(self, value: str) -> str:
        normalized = (value or "dark").strip().lower()
        if normalized not in {"light", "dark", "system"}:
            return "dark"
        return normalized

    def t(self, key: str, **kwargs: Any) -> str:
        return self.translator.translate(key, **kwargs)

    def _appearance_choice_map(self) -> dict[str, str]:
        return {
            self.t("appearance.light"): "light",
            self.t("appearance.dark"): "dark",
            self.t("appearance.system"): "system",
        }

    def _appearance_label_to_mode(self, value: str) -> str:
        mapping = self._appearance_choice_map()
        return mapping.get(value, self._normalize_appearance_mode(value))

    def _appearance_mode_to_label(self, value: str) -> str:
        normalized = self._normalize_appearance_mode(value)
        reverse_mapping = {mode: label for label, mode in self._appearance_choice_map().items()}
        return reverse_mapping.get(normalized, self.t("appearance.dark"))

    def _apply_user_appearance_mode(self, mode: str) -> None:
        ctk.set_appearance_mode(self._normalize_appearance_mode(mode))

    def _ui_language_options(self) -> list[str]:
        return list(SUPPORTED_UI_LANGUAGES.values())

    def _selected_ui_language_code(self) -> str:
        return ui_language_code_from_label(self.ui_language_var.get())

    def _capture_ui_state(self) -> UIStateSnapshot:
        snapshot = UIStateSnapshot()
        if hasattr(self, "topic_text"):
            try:
                snapshot.topic_text = self.topic_text.get("1.0", "end-1c")
            except tk.TclError:
                snapshot.topic_text = ""
        if hasattr(self, "setup_summary_label"):
            snapshot.setup_summary = str(self.setup_summary_label.cget("text"))
        if hasattr(self, "status_label"):
            snapshot.status_text = str(self.status_label.cget("text"))
            snapshot.status_color = self.status_label.cget("text_color")
        if hasattr(self, "connection_chip"):
            snapshot.connection_chip = str(self.connection_chip.cget("text"))
        if hasattr(self, "render_chip"):
            snapshot.render_chip = str(self.render_chip.cget("text"))
        if hasattr(self, "progress_bar"):
            snapshot.progress_value = float(self.progress_bar.get())
        if hasattr(self, "progress_percent_label"):
            snapshot.progress_percent = str(self.progress_percent_label.cget("text"))
        if hasattr(self, "progress_detail_label"):
            snapshot.progress_detail = str(self.progress_detail_label.cget("text"))
        if hasattr(self, "countdown_label"):
            snapshot.countdown_text = str(self.countdown_label.cget("text"))
        return snapshot

    def _format_setup_summary(self, status: SetupStatus) -> str:
        lines = [
            f"LM Studio: {'Ready' if status.lmstudio_installed else 'Pending'}" if self._selected_ui_language_code() == "en" else f"LM Studio: {'Listo' if status.lmstudio_installed else 'Pendiente'}",
            f"ComfyUI Desktop: {'Ready' if status.comfyui_installed else 'Pending'}" if self._selected_ui_language_code() == "en" else f"ComfyUI Desktop: {'Listo' if status.comfyui_installed else 'Pendiente'}",
            f"FFmpeg: {'Ready' if status.ffmpeg_ready else 'Pending'}" if self._selected_ui_language_code() == "en" else f"FFmpeg: {'Listo' if status.ffmpeg_ready else 'Pendiente'}",
            f"ComfyUI API: {'Connected' if status.comfyui_reachable else 'Offline'}" if self._selected_ui_language_code() == "en" else f"ComfyUI API: {'Conectado' if status.comfyui_reachable else 'Sin conexion'}",
            f"Automatic workflow: {'Ready' if status.workflow_ready else 'Pending'}" if self._selected_ui_language_code() == "en" else f"Workflow automatico: {'Listo' if status.workflow_ready else 'Pendiente'}",
            f"Windows local voice: {'Available' if status.windows_tts_ready else 'Unavailable'}" if self._selected_ui_language_code() == "en" else f"Voz local de Windows: {'Disponible' if status.windows_tts_ready else 'No disponible'}",
        ]
        if status.gpu_names:
            lines.append(f"GPUs detected: {len(status.gpu_names)}" if self._selected_ui_language_code() == "en" else f"GPUs detectadas: {len(status.gpu_names)}")
        if status.comfyui_checkpoint:
            lines.append(f"Visual model: {status.comfyui_checkpoint}" if self._selected_ui_language_code() == "en" else f"Modelo visual: {status.comfyui_checkpoint}")
        if status.comfyui_base_url:
            lines.append(f"ComfyUI URL: {status.comfyui_base_url}")
        if status.comfyui_worker_urls:
            lines.append(f"ComfyUI workers: {len(status.comfyui_worker_urls)}" if self._selected_ui_language_code() == "en" else f"Workers ComfyUI: {len(status.comfyui_worker_urls)}")
        if status.model_folder:
            lines.append(f"Shared models: {status.model_folder}" if self._selected_ui_language_code() == "en" else f"Modelos compartidos: {status.model_folder}")
        if status.notes:
            lines.extend(status.notes)
        return "\n".join(lines)

    def _restore_ui_state(self, snapshot: UIStateSnapshot) -> None:
        if hasattr(self, "topic_text") and snapshot.topic_text:
            self.topic_text.delete("1.0", "end")
            self.topic_text.insert("1.0", snapshot.topic_text)
        if hasattr(self, "setup_summary_label") and snapshot.setup_summary:
            self.setup_summary_label.configure(text=snapshot.setup_summary)
        if hasattr(self, "status_label") and snapshot.status_text:
            self.status_label.configure(text=snapshot.status_text)
            if snapshot.status_color is not None:
                self.status_label.configure(text_color=snapshot.status_color)
        if hasattr(self, "connection_chip") and snapshot.connection_chip:
            self.connection_chip.configure(text=snapshot.connection_chip)
        if hasattr(self, "render_chip") and snapshot.render_chip:
            self.render_chip.configure(text=snapshot.render_chip)
        if hasattr(self, "progress_bar"):
            self.progress_bar.set(snapshot.progress_value)
        if hasattr(self, "progress_percent_label") and snapshot.progress_percent:
            self.progress_percent_label.configure(text=snapshot.progress_percent)
        if hasattr(self, "progress_detail_label") and snapshot.progress_detail:
            self.progress_detail_label.configure(text=snapshot.progress_detail)
        if hasattr(self, "countdown_label") and snapshot.countdown_text:
            self.countdown_label.configure(text=snapshot.countdown_text)

    def _rebuild_translated_ui(self) -> None:
        snapshot = self._capture_ui_state()
        self._tooltips = []
        for widget_name in ["sidebar", "main_panel", "status_bar"]:
            widget = getattr(self, widget_name, None)
            if widget is not None:
                widget.destroy()
        self._build_menu()
        self._build_layout()
        self._populate_detected_gpu_options()
        self._sync_video_provider_ui()
        self._sync_tts_ui()
        self._load_history_buttons()
        if self.current_project:
            self._render_project(self.current_project)
        self._restore_ui_state(snapshot)
        if not snapshot.status_text:
            self._set_status(self.t("app.ready"))
        if not snapshot.setup_summary and hasattr(self, "setup_summary_label"):
            self.setup_summary_label.configure(text=self.t("status.setup_summary_initial"))
        if not snapshot.connection_chip:
            self.connection_chip.configure(text=self.t("status.testing_connection"))
        if not snapshot.render_chip:
            self.render_chip.configure(text=self.t("status.render_chip", provider=self.video_provider_var.get()))
        if not snapshot.progress_detail:
            self._set_progress_ui(0, self.t("progress.waiting_detail"))
        self._update_countdown_label()

    def _on_ui_language_change(self) -> None:
        language_code = self._selected_ui_language_code()
        if language_code == self.app_config.ui_language:
            return
        current_appearance_mode = self._appearance_label_to_mode(self.appearance_mode_var.get())
        self.translator.set_language(language_code)
        self.ui_language_var.set(ui_language_label(language_code))
        self.appearance_mode_var.set(self._appearance_mode_to_label(current_appearance_mode))
        self.config_manager.update(ui_language=language_code)
        self.app_config = self.config_manager.config
        self._rebuild_translated_ui()
        self._schedule_save()
        self._set_status(self.t("status.interface_language_updated", language=self.ui_language_var.get()), success=True)

    def _create_variables(self) -> None:
        self.ui_language_var = tk.StringVar(value=ui_language_label(self.app_config.ui_language))
        self.appearance_mode_var = tk.StringVar(value=self._appearance_mode_to_label(self.app_config.appearance_mode))
        self.base_url_var = tk.StringVar(value=self.app_config.lmstudio_base_url)
        self.model_var = tk.StringVar(value=self.app_config.model)
        self.api_key_var = tk.StringVar(value=self.app_config.api_key)
        self.video_provider_var = tk.StringVar(value=self.app_config.video_provider)
        initial_aspect_ratio = aspect_ratio_for_video_format(
            self.app_config.video_format,
            fallback=self.app_config.video_aspect_ratio or "9:16",
        )
        self.video_aspect_ratio_var = tk.StringVar(value=initial_aspect_ratio)
        self.render_captions_var = tk.BooleanVar(value=self.app_config.render_captions)
        self.comfyui_base_url_var = tk.StringVar(value=self.app_config.comfyui_base_url)
        self.comfyui_worker_urls_var = tk.StringVar(value=self.app_config.comfyui_worker_urls)
        self.parallel_scene_workers_var = tk.StringVar(value=str(self.app_config.parallel_scene_workers))
        self.render_gpu_var = tk.StringVar(value=self.app_config.render_gpu_preference or "Auto")
        self.video_render_device_var = tk.StringVar(value=self.app_config.video_render_device_preference or "Auto")
        self.video_encoder_var = tk.StringVar(value=self.app_config.video_encoder_preference or "Auto")
        self.comfyui_checkpoint_var = tk.StringVar(value=self.app_config.comfyui_checkpoint)
        self.comfyui_workflow_path_var = tk.StringVar(value=self.app_config.comfyui_workflow_path)
        self.comfyui_negative_prompt_var = tk.StringVar(value=self.app_config.comfyui_negative_prompt)
        self.comfyui_poll_interval_var = tk.StringVar(value=str(self.app_config.comfyui_poll_interval_seconds))
        self.tts_backend_var = tk.StringVar(value=self.app_config.tts_backend)
        self.ffmpeg_path_var = tk.StringVar(value=self.app_config.ffmpeg_path)
        self.piper_executable_path_var = tk.StringVar(value=self.app_config.piper_executable_path)
        self.piper_model_path_var = tk.StringVar(value=self.app_config.piper_model_path)
        self.avatar_source_image_path_var = tk.StringVar(value=self.app_config.avatar_source_image_path)
        self.temperature_var = tk.DoubleVar(value=self.app_config.temperature)
        self.scene_count_var = tk.StringVar(value=str(self.app_config.scene_count))
        self.language_var = tk.StringVar(value=self.app_config.output_language)
        self.duration_var = tk.StringVar(value=str(self.app_config.estimated_duration_seconds))
        self.visual_style_var = tk.StringVar(value=self.app_config.visual_style)
        self.audience_var = tk.StringVar(value=self.app_config.audience)
        self.tone_var = tk.StringVar(value=self.app_config.narrative_tone)
        self.format_var = tk.StringVar(value=self.app_config.video_format)
        self.mode_var = tk.StringVar(value=self.app_config.generation_mode)
        self.output_dir_var = tk.StringVar(value=self.app_config.output_dir)
        self.auto_start_var = tk.BooleanVar(value=self.app_config.auto_start_enabled)
        self.auto_close_var = tk.BooleanVar(value=self.app_config.auto_close_enabled)
        self.auto_close_seconds_var = tk.StringVar(value=str(self.app_config.auto_close_seconds))
        self.max_tokens_var = tk.StringVar(value=str(self.app_config.max_tokens))
        self.timeout_var = tk.StringVar(value=str(self.app_config.request_timeout_seconds))
        self.retries_var = tk.StringVar(value=str(self.app_config.json_retry_attempts))
        self.show_api_key_var = tk.BooleanVar(value=False)

        for variable in [
            self.appearance_mode_var,
            self.base_url_var,
            self.model_var,
            self.api_key_var,
            self.video_provider_var,
            self.video_aspect_ratio_var,
            self.render_captions_var,
            self.comfyui_base_url_var,
            self.comfyui_worker_urls_var,
            self.parallel_scene_workers_var,
            self.render_gpu_var,
            self.video_render_device_var,
            self.video_encoder_var,
            self.comfyui_checkpoint_var,
            self.comfyui_workflow_path_var,
            self.comfyui_negative_prompt_var,
            self.comfyui_poll_interval_var,
            self.tts_backend_var,
            self.ffmpeg_path_var,
            self.piper_executable_path_var,
            self.piper_model_path_var,
            self.avatar_source_image_path_var,
            self.temperature_var,
            self.scene_count_var,
            self.language_var,
            self.duration_var,
            self.visual_style_var,
            self.audience_var,
            self.tone_var,
            self.format_var,
            self.mode_var,
            self.output_dir_var,
            self.auto_start_var,
            self.auto_close_var,
            self.auto_close_seconds_var,
            self.max_tokens_var,
            self.timeout_var,
            self.retries_var,
        ]:
            variable.trace_add("write", lambda *_: self._schedule_save())

        self.tts_backend_var.trace_add("write", lambda *_: self._sync_tts_ui())
        self.video_render_device_var.trace_add("write", lambda *_: self._schedule_render_selection_summary_update())
        self.video_encoder_var.trace_add("write", lambda *_: self._schedule_render_selection_summary_update())
        self.ffmpeg_path_var.trace_add("write", lambda *_: self._schedule_render_capability_refresh())
        self.format_var.trace_add("write", lambda *_: self._sync_video_format_preferences())

    def _build_menu(self) -> None:
        menu_bar = tk.Menu(self)

        file_menu = tk.Menu(menu_bar, tearoff=0)
        file_menu.add_command(label=self.t("menu.items.new"), accelerator="Ctrl+N", command=self.reset_form)
        file_menu.add_command(label=self.t("menu.items.inspect_environment"), accelerator="Ctrl+I", command=self.inspect_environment)
        file_menu.add_command(label=self.t("menu.items.prepare_environment"), accelerator="Ctrl+Shift+I", command=self.prepare_environment)
        file_menu.add_command(label=self.t("menu.items.test_connection"), accelerator="Ctrl+L", command=self.test_connection)
        file_menu.add_command(label=self.t("menu.items.test_comfyui"), accelerator="Ctrl+H", command=self.test_local_video_connection)
        file_menu.add_command(label=self.t("menu.items.generate_script"), accelerator="Ctrl+G", command=self.start_generation)
        file_menu.add_command(label=self.t("menu.items.generate_full_video"), accelerator="Ctrl+Shift+G", command=self.generate_full_video)
        file_menu.add_separator()
        file_menu.add_command(label=self.t("menu.items.export_json"), accelerator="Ctrl+J", command=self.export_json)
        file_menu.add_command(label=self.t("menu.items.export_txt"), accelerator="Ctrl+T", command=self.export_txt)
        file_menu.add_command(label=self.t("menu.items.export_csv"), accelerator="Ctrl+E", command=self.export_csv)
        file_menu.add_command(label=self.t("menu.items.generate_video"), accelerator="Ctrl+M", command=self.generate_video)
        file_menu.add_separator()
        file_menu.add_command(label=self.t("menu.items.open_output"), accelerator="Ctrl+O", command=self.open_output_folder)
        file_menu.add_command(label=self.t("menu.items.exit"), accelerator="Ctrl+Q", command=self._on_close)
        menu_bar.add_cascade(label=self.t("menu.file"), menu=file_menu)

        help_menu = tk.Menu(menu_bar, tearoff=0)
        help_menu.add_command(label=self.t("menu.items.about"), accelerator="F1", command=self.show_about_dialog)
        menu_bar.add_cascade(label=self.t("menu.view"), menu=self._build_view_menu(menu_bar))
        menu_bar.add_cascade(label=self.t("menu.help"), menu=help_menu)
        self.config(menu=menu_bar)

    def _build_view_menu(self, menu_bar: tk.Menu) -> tk.Menu:
        view_menu = tk.Menu(menu_bar, tearoff=0)
        view_menu.add_radiobutton(label=self.t("appearance.dark"), value=self.t("appearance.dark"), variable=self.appearance_mode_var, command=self._on_appearance_change)
        view_menu.add_radiobutton(label=self.t("appearance.light"), value=self.t("appearance.light"), variable=self.appearance_mode_var, command=self._on_appearance_change)
        view_menu.add_radiobutton(label=self.t("appearance.system"), value=self.t("appearance.system"), variable=self.appearance_mode_var, command=self._on_appearance_change)
        view_menu.add_separator()
        language_menu = tk.Menu(view_menu, tearoff=0)
        for code in SUPPORTED_UI_LANGUAGES:
            label = ui_language_label(code)
            language_menu.add_radiobutton(label=label, value=label, variable=self.ui_language_var, command=self._on_ui_language_change)
        view_menu.add_cascade(label=self.t("labels.interface_language"), menu=language_menu)
        view_menu.add_separator()
        view_menu.add_command(label=self.t("menu.items.toggle_theme"), accelerator="Ctrl+Shift+D", command=self.toggle_dark_mode)
        return view_menu

    def _bind_shortcuts(self) -> None:
        self.bind_all("<Control-n>", lambda event: self.reset_form())
        self.bind_all("<Control-i>", lambda event: self.inspect_environment())
        self.bind_all("<Control-Shift-I>", lambda event: self.prepare_environment())
        self.bind_all("<Control-Shift-i>", lambda event: self.prepare_environment())
        self.bind_all("<Control-l>", lambda event: self.test_connection())
        self.bind_all("<Control-h>", lambda event: self.test_local_video_connection())
        self.bind_all("<Control-g>", lambda event: self.start_generation())
        self.bind_all("<Control-Shift-G>", lambda event: self.generate_full_video())
        self.bind_all("<Control-Shift-g>", lambda event: self.generate_full_video())
        self.bind_all("<Control-j>", lambda event: self.export_json())
        self.bind_all("<Control-t>", lambda event: self.export_txt())
        self.bind_all("<Control-e>", lambda event: self.export_csv())
        self.bind_all("<Control-m>", lambda event: self.generate_video())
        self.bind_all("<Control-o>", lambda event: self.open_output_folder())
        self.bind_all("<Control-q>", lambda event: self._on_close())
        self.bind_all("<Control-Shift-D>", lambda event: self.toggle_dark_mode())
        self.bind_all("<Control-Shift-d>", lambda event: self.toggle_dark_mode())
        self.bind_all("<F1>", lambda event: self.show_about_dialog())

    def _bind_activity_reset(self) -> None:
        self.bind_all("<KeyPress>", lambda event: self._reset_auto_close_timer())
        self.bind_all("<ButtonPress>", lambda event: self._reset_auto_close_timer())
        self.bind("<Configure>", self._on_configure)

    def _build_layout(self) -> None:
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=0)

        self.sidebar = ctk.CTkScrollableFrame(
            self,
            width=410,
            fg_color=THEME["sidebar"],
            corner_radius=26,
            border_width=0,
        )
        self.sidebar.grid(row=0, column=0, padx=(18, 10), pady=(18, 10), sticky="nsew")
        self.sidebar.grid_columnconfigure(0, weight=1)

        self.main_panel = ctk.CTkFrame(self, fg_color=THEME["main_panel"], corner_radius=28)
        self.main_panel.grid(row=0, column=1, padx=(10, 18), pady=(18, 10), sticky="nsew")
        self.main_panel.grid_columnconfigure(0, weight=1)
        self.main_panel.grid_rowconfigure(2, weight=1)

        self.status_bar = ctk.CTkFrame(self, fg_color=THEME["status_bar"], corner_radius=18, height=42)
        self.status_bar.grid(row=1, column=0, columnspan=2, padx=18, pady=(0, 18), sticky="ew")
        self.status_bar.grid_columnconfigure(0, weight=1)
        self.status_label = ctk.CTkLabel(
            self.status_bar,
            text="",
            text_color=THEME["status_default"],
            font=ctk.CTkFont("Segoe UI", 13),
        )
        self.status_label.grid(row=0, column=0, padx=16, pady=8, sticky="w")
        self.countdown_label = ctk.CTkLabel(
            self.status_bar,
            text=self.t("countdown.off", version=DISPLAY_VERSION),
            text_color=ui_color("#93C5FD", "#67E8F9"),
            font=ctk.CTkFont("Segoe UI", 13, weight="bold"),
        )
        self.countdown_label.grid(row=0, column=1, padx=16, pady=8, sticky="e")

        self._build_sidebar()
        self._build_main_panel()

    def _build_sidebar(self) -> None:
        hero = ctk.CTkFrame(self.sidebar, fg_color=THEME["hero"], corner_radius=26)
        hero.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 14))
        hero.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            hero,
            text=APP_NAME,
            text_color=THEME["hero_text"],
            font=ctk.CTkFont("Segoe UI Variable Display", 28, weight="bold"),
        ).grid(row=0, column=0, sticky="w", padx=18, pady=(16, 4))
        ctk.CTkLabel(
            hero,
            text=self.t("hero.subtitle", version=DISPLAY_VERSION),
            text_color=THEME["accent"],
            font=ctk.CTkFont("Segoe UI", 13),
        ).grid(row=1, column=0, sticky="w", padx=18, pady=(0, 4))
        ctk.CTkLabel(
            hero,
            text=self.t("hero.description"),
            text_color=THEME["soft_text"],
            justify="left",
            wraplength=340,
            font=ctk.CTkFont("Segoe UI", 14),
        ).grid(row=2, column=0, sticky="w", padx=18, pady=(0, 16))

        row = 1
        topic_card = self._make_card(self.sidebar, self.t("cards.project_brief.title"), self.t("cards.project_brief.subtitle"))
        topic_card.grid(row=row, column=0, sticky="ew", padx=10, pady=8)
        self.topic_text = ctk.CTkTextbox(
            topic_card,
            height=120,
            fg_color=THEME["input_bg"],
            text_color=THEME["primary_text"],
            border_width=1,
            border_color=THEME["input_border"],
        )
        self.topic_text.grid(row=2, column=0, padx=14, pady=(4, 14), sticky="ew")
        self.topic_text.insert("1.0", self.app_config.video_topic)
        self.topic_text.bind("<KeyRelease>", lambda event: self._schedule_save())
        row += 1

        setup_card = self._make_card(self.sidebar, self.t("cards.setup.title"), self.t("cards.setup.subtitle"))
        setup_card.grid(row=row, column=0, sticky="ew", padx=10, pady=8)
        self.setup_summary_label = ctk.CTkLabel(
            setup_card,
            text=self.t("status.setup_summary_initial"),
            text_color=ui_color("#C7D2FE", "#C7D2FE"),
            justify="left",
            wraplength=330,
            font=ctk.CTkFont("Segoe UI", 12),
        )
        self.setup_summary_label.grid(row=2, column=0, sticky="w", padx=14, pady=(4, 10))
        self.inspect_button = ctk.CTkButton(
            setup_card,
            text=self.t("buttons.inspect_environment"),
            command=self.inspect_environment,
            fg_color=ui_color("#0369A1", "#0369A1"),
            hover_color=ui_color("#075985", "#075985"),
        )
        self.inspect_button.grid(row=3, column=0, sticky="ew", padx=14, pady=(0, 8))
        self.prepare_button = ctk.CTkButton(
            setup_card,
            text=self.t("buttons.prepare_environment"),
            command=self.prepare_environment,
            fg_color=ui_color("#15803D", "#15803D"),
            hover_color=ui_color("#166534", "#166534"),
        )
        self.prepare_button.grid(row=4, column=0, sticky="ew", padx=14, pady=(0, 8))
        self.launch_lmstudio_button = ctk.CTkButton(
            setup_card,
            text=self.t("buttons.launch_lmstudio"),
            command=self.launch_lmstudio,
            fg_color=ui_color("#4F46E5", "#4F46E5"),
            hover_color=ui_color("#4338CA", "#4338CA"),
        )
        self.launch_lmstudio_button.grid(row=5, column=0, sticky="ew", padx=14, pady=(0, 8))
        self.launch_comfyui_button = ctk.CTkButton(
            setup_card,
            text=self.t("buttons.launch_comfyui"),
            command=self.launch_comfyui,
            fg_color=ui_color("#7C3AED", "#7C3AED"),
            hover_color=ui_color("#6D28D9", "#6D28D9"),
        )
        self.launch_comfyui_button.grid(row=6, column=0, sticky="ew", padx=14, pady=(0, 10))
        self.install_model_button = ctk.CTkButton(
            setup_card,
            text=self.t("buttons.install_checkpoint"),
            command=self.install_recommended_checkpoint,
            fg_color=ui_color("#B45309", "#B45309"),
            hover_color=ui_color("#92400E", "#92400E"),
        )
        self.install_model_button.grid(row=7, column=0, sticky="ew", padx=14, pady=(0, 8))
        self.open_models_button = ctk.CTkButton(
            setup_card,
            text=self.t("buttons.open_models_folder"),
            command=self.open_comfyui_models_folder,
            fg_color=ui_color("#1E293B", "#334155"),
            hover_color=ui_color("#0F172A", "#1E293B"),
        )
        self.open_models_button.grid(row=8, column=0, sticky="ew", padx=14, pady=(0, 10))
        row += 1

        profile_card = self._make_card(self.sidebar, self.t("cards.quick_setup.title"), self.t("cards.quick_setup.subtitle"))
        profile_card.grid(row=row, column=0, sticky="ew", padx=10, pady=8)
        self._make_labeled_entry(profile_card, 2, self.t("labels.visual_style"), self.visual_style_var)
        self._make_labeled_combo(profile_card, 4, self.t("labels.audience"), self.audience_var, ["General", "Gamers", "Students", "Professionals", "Children"])
        self._make_labeled_combo(profile_card, 6, self.t("labels.narrative_tone"), self.tone_var, ["Cinematic and immersive", "Educational", "Epic", "Emotional", "Fast-paced"])
        self._make_labeled_combo(profile_card, 8, self.t("labels.video_format"), self.format_var, ["YouTube Short", "TikTok", "Instagram Reel", "YouTube Long", "Trailer"])
        self._make_labeled_entry(profile_card, 10, self.t("labels.duration"), self.duration_var)
        self.video_provider_combo = self._make_labeled_combo(
            profile_card,
            12,
            self.t("labels.render_backend"),
            self.video_provider_var,
            ["Storyboard local", "Local AI video", "Local Avatar video"],
        )
        self.video_provider_combo.configure(command=lambda _value: self._on_video_provider_change())
        self._make_labeled_combo(
            profile_card,
            14,
            self.t("labels.aspect_ratio"),
            self.video_aspect_ratio_var,
            ["9:16", "16:9", "1:1"],
        )
        self.render_captions_checkbox = ctk.CTkCheckBox(
            profile_card,
            text=self.t("checkbox.render_captions"),
            variable=self.render_captions_var,
            checkbox_width=20,
            checkbox_height=20,
            text_color=THEME["hero_text"],
        )
        self.render_captions_checkbox.grid(row=16, column=0, sticky="w", padx=14, pady=(4, 8))
        self.quick_generate_button = ctk.CTkButton(
            profile_card,
            text=self.t("buttons.generate_full_video"),
            command=self.generate_full_video,
            height=44,
            fg_color=ui_color("#EA580C", "#EA580C"),
            hover_color=ui_color("#C2410C", "#C2410C"),
        )
        self.quick_generate_button.grid(row=17, column=0, sticky="ew", padx=14, pady=(2, 12))
        ctk.CTkLabel(
            profile_card,
            text=self.t("quick_setup.tip"),
            text_color=ui_color("#94A3B8", "#94A3B8"),
            wraplength=330,
            justify="left",
            font=ctk.CTkFont("Segoe UI", 12),
        ).grid(row=18, column=0, sticky="w", padx=14, pady=(0, 10))
        row += 1

        lm_card = self._make_card(self.sidebar, self.t("cards.lmstudio.title"), self.t("cards.lmstudio.subtitle"))
        lm_card.grid(row=row, column=0, sticky="ew", padx=10, pady=8)
        self._make_labeled_entry(lm_card, 2, self.t("labels.base_url"), self.base_url_var)
        self.model_combo = self._make_labeled_combo(lm_card, 4, self.t("labels.model"), self.model_var, [""])

        ctk.CTkLabel(
            lm_card,
            text=self.t("labels.api_key_optional"),
            text_color=THEME["card_label"],
            font=ctk.CTkFont("Segoe UI", 13, weight="bold"),
        ).grid(row=6, column=0, sticky="w", padx=14, pady=(10, 4))
        api_row = ctk.CTkFrame(lm_card, fg_color="transparent")
        api_row.grid(row=7, column=0, padx=14, pady=(0, 8), sticky="ew")
        api_row.grid_columnconfigure(0, weight=1)
        self.api_key_entry = ctk.CTkEntry(api_row, textvariable=self.api_key_var, show="*", fg_color=THEME["input_bg"], text_color=THEME["primary_text"])
        self.api_key_entry.grid(row=0, column=0, sticky="ew")
        self.show_api_key_button = ctk.CTkButton(
            api_row,
            text=self.t("buttons.show"),
            width=90,
            command=self.toggle_api_key_visibility,
            fg_color=ui_color("#1D4ED8", "#2563EB"),
            hover_color=ui_color("#1E40AF", "#1D4ED8"),
        )
        self.show_api_key_button.grid(row=0, column=1, padx=(10, 0))

        slider_row = ctk.CTkFrame(lm_card, fg_color="transparent")
        slider_row.grid(row=8, column=0, padx=14, pady=(6, 2), sticky="ew")
        slider_row.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(slider_row, text=self.t("labels.temperature"), text_color=THEME["card_label"], font=ctk.CTkFont("Segoe UI", 13, weight="bold")).grid(row=0, column=0, sticky="w")
        self.temperature_value_label = ctk.CTkLabel(slider_row, text=f"{self.temperature_var.get():.2f}", text_color=THEME["accent"])
        self.temperature_value_label.grid(row=0, column=1, sticky="e")
        self.temperature_slider = ctk.CTkSlider(
            lm_card,
            from_=0.0,
            to=1.5,
            number_of_steps=30,
            variable=self.temperature_var,
            command=self._on_temperature_change,
            button_color=ui_color("#F97316", "#FB923C"),
            progress_color=ui_color("#F59E0B", "#F97316"),
        )
        self.temperature_slider.grid(row=9, column=0, padx=14, pady=(0, 10), sticky="ew")

        self._make_labeled_entry(lm_card, 10, self.t("labels.scene_count"), self.scene_count_var)
        self._make_labeled_combo(lm_card, 12, self.t("labels.output_language"), self.language_var, ["Espanol", "English", "Portugues", "Frances"])
        row += 1

        self.local_ai_card = self._make_card(self.sidebar, self.t("cards.local_ai.title"), self.t("cards.local_ai.subtitle"))
        self.local_ai_card.grid(row=row, column=0, sticky="ew", padx=10, pady=8)
        self._make_labeled_entry(self.local_ai_card, 2, self.t("labels.comfyui_base_url"), self.comfyui_base_url_var)
        self._make_labeled_entry(self.local_ai_card, 4, self.t("labels.comfyui_workers"), self.comfyui_worker_urls_var)
        self._make_labeled_entry(self.local_ai_card, 6, self.t("labels.parallel_workers"), self.parallel_scene_workers_var)
        self.render_gpu_combo = self._make_labeled_combo(self.local_ai_card, 8, self.t("labels.comfyui_gpu"), self.render_gpu_var, ["Auto"])
        self.comfyui_checkpoint_combo = self._make_labeled_combo(self.local_ai_card, 10, self.t("labels.visual_model"), self.comfyui_checkpoint_var, [""])
        self._make_labeled_entry(self.local_ai_card, 12, self.t("labels.workflow_json"), self.comfyui_workflow_path_var)
        workflow_button = ctk.CTkButton(
            self.local_ai_card,
            text=self.t("buttons.browse_workflow"),
            command=self.choose_comfyui_workflow,
            fg_color=ui_color("#0F766E", "#0F766E"),
            hover_color=ui_color("#115E59", "#134E4A"),
        )
        workflow_button.grid(row=14, column=0, sticky="ew", padx=14, pady=(0, 10))
        self._make_labeled_entry(self.local_ai_card, 16, self.t("labels.negative_prompt"), self.comfyui_negative_prompt_var)
        self._make_labeled_entry(self.local_ai_card, 18, self.t("labels.ffmpeg_path"), self.ffmpeg_path_var)
        self.video_render_device_combo = self._make_labeled_combo(
            self.local_ai_card,
            20,
            self.t("labels.video_render_device"),
            self.video_render_device_var,
            ["Auto", "CPU only"],
        )
        self.video_encoder_combo = self._make_labeled_combo(
            self.local_ai_card,
            22,
            self.t("labels.video_encoder"),
            self.video_encoder_var,
            ["Auto", "libx264"],
        )
        self.detected_gpus_summary_label = ctk.CTkLabel(
            self.local_ai_card,
            text=self.t("local_ai.no_gpu_detected"),
            text_color=ui_color("#94A3B8", "#94A3B8"),
            wraplength=330,
            justify="left",
            font=ctk.CTkFont("Segoe UI", 12),
        )
        self.detected_gpus_summary_label.grid(row=24, column=0, sticky="w", padx=14, pady=(2, 2))
        self.active_encoder_summary_label = ctk.CTkLabel(
            self.local_ai_card,
            text=self.t("local_ai.active_encoder_pending"),
            text_color=ui_color("#CBD5E1", "#CBD5E1"),
            wraplength=330,
            justify="left",
            font=ctk.CTkFont("Segoe UI", 12, weight="bold"),
        )
        self.active_encoder_summary_label.grid(row=25, column=0, sticky="w", padx=14, pady=(0, 6))
        self._tooltips.append(HoverToolTip(self.video_render_device_combo, lambda: self.t("tooltips.video_render_device")))
        self._tooltips.append(HoverToolTip(self.video_encoder_combo, lambda: self.t("tooltips.video_encoder")))
        self._make_labeled_entry(self.local_ai_card, 27, self.t("labels.comfyui_poll_interval"), self.comfyui_poll_interval_var)
        self._make_labeled_combo(self.local_ai_card, 29, self.t("labels.tts_backend"), self.tts_backend_var, ["Windows local", "Sin voz", "Piper local"])
        self.piper_executable_entry = self._make_labeled_entry(self.local_ai_card, 31, self.t("labels.piper_executable"), self.piper_executable_path_var)
        self.piper_model_entry = self._make_labeled_entry(self.local_ai_card, 33, self.t("labels.piper_model"), self.piper_model_path_var)
        self.piper_button = ctk.CTkButton(
            self.local_ai_card,
            text=self.t("buttons.browse_piper_model"),
            command=self.choose_piper_model,
            fg_color=ui_color("#2563EB", "#1D4ED8"),
            hover_color=ui_color("#1E40AF", "#1E3A8A"),
        )
        self.piper_button.grid(row=35, column=0, sticky="ew", padx=14, pady=(0, 6))
        self.avatar_source_image_entry = self._make_labeled_entry(self.local_ai_card, 37, self.t("labels.avatar_source_image"), self.avatar_source_image_path_var)
        self.avatar_source_image_button = ctk.CTkButton(
            self.local_ai_card,
            text=self.t("buttons.browse_avatar_image"),
            command=self.choose_avatar_image,
            fg_color=ui_color("#9333EA", "#7E22CE"),
            hover_color=ui_color("#7E22CE", "#6B21A8"),
        )
        self.avatar_source_image_button.grid(row=39, column=0, sticky="ew", padx=14, pady=(0, 6))
        ctk.CTkLabel(
            self.local_ai_card,
            text=self.t("local_ai.tip"),
            text_color=ui_color("#94A3B8", "#94A3B8"),
            wraplength=330,
            justify="left",
            font=ctk.CTkFont("Segoe UI", 12),
        ).grid(row=40, column=0, sticky="w", padx=14, pady=(0, 10))
        row += 1

        advanced_card = self._make_card(self.sidebar, self.t("cards.advanced.title"), self.t("cards.advanced.subtitle"))
        advanced_card.grid(row=row, column=0, sticky="ew", padx=10, pady=8)
        self.appearance_combo = self._make_labeled_combo(advanced_card, 2, self.t("labels.appearance_mode"), self.appearance_mode_var, list(self._appearance_choice_map().keys()))
        self.appearance_combo.configure(command=lambda _value: self._on_appearance_change())
        self.ui_language_combo = self._make_labeled_combo(advanced_card, 4, self.t("labels.interface_language"), self.ui_language_var, self._ui_language_options())
        self.ui_language_combo.configure(command=lambda _value: self._on_ui_language_change())
        self._make_labeled_entry(advanced_card, 6, self.t("labels.output_folder"), self.output_dir_var)
        browse_button = ctk.CTkButton(
            advanced_card,
            text=self.t("buttons.browse"),
            command=self.choose_output_folder,
            fg_color=ui_color("#0F766E", "#0F766E"),
            hover_color=ui_color("#115E59", "#134E4A"),
        )
        browse_button.grid(row=8, column=0, sticky="ew", padx=14, pady=(0, 10))

        self.auto_start_checkbox = ctk.CTkCheckBox(
            advanced_card,
            text=self.t("checkbox.auto_start"),
            variable=self.auto_start_var,
            checkbox_width=20,
            checkbox_height=20,
            text_color=THEME["hero_text"],
        )
        self.auto_start_checkbox.grid(row=9, column=0, sticky="w", padx=14, pady=(2, 6))

        self.auto_close_checkbox = ctk.CTkCheckBox(
            advanced_card,
            text=self.t("checkbox.auto_close"),
            variable=self.auto_close_var,
            checkbox_width=20,
            checkbox_height=20,
            text_color=THEME["hero_text"],
        )
        self.auto_close_checkbox.grid(row=10, column=0, sticky="w", padx=14, pady=(2, 6))

        self._make_labeled_entry(advanced_card, 11, self.t("labels.auto_close_seconds"), self.auto_close_seconds_var)
        self._make_labeled_entry(advanced_card, 13, self.t("labels.json_retry_attempts"), self.retries_var)
        self._make_labeled_entry(advanced_card, 15, self.t("labels.request_timeout"), self.timeout_var)
        self._make_labeled_entry(advanced_card, 17, self.t("labels.max_tokens"), self.max_tokens_var)
        row += 1

        actions_card = self._make_card(self.sidebar, self.t("cards.actions.title"), self.t("cards.actions.subtitle"))
        actions_card.grid(row=row, column=0, sticky="ew", padx=10, pady=(8, 18))
        self.connection_button = self._make_action_button(actions_card, 2, self.t("buttons.test_connection"), "#0284C7", "#0369A1", self.test_connection)
        self.local_video_button = self._make_action_button(actions_card, 3, self.t("buttons.test_comfyui"), "#0F766E", "#115E59", self.test_local_video_connection)
        self.generate_button = self._make_action_button(actions_card, 4, self.t("buttons.generate_script"), "#EA580C", "#C2410C", self.start_generation)
        self.export_json_button = self._make_action_button(actions_card, 5, self.t("buttons.export_json"), "#4F46E5", "#4338CA", self.export_json)
        self.export_txt_button = self._make_action_button(actions_card, 6, self.t("buttons.export_txt"), "#2563EB", "#1D4ED8", self.export_txt)
        self.export_csv_button = self._make_action_button(actions_card, 7, self.t("buttons.export_csv"), "#0F766E", "#115E59", self.export_csv)
        self.video_button = self._make_action_button(actions_card, 8, self.t("buttons.generate_video"), "#B45309", "#92400E", self.generate_video)
        self.folder_button = self._make_action_button(actions_card, 9, self.t("buttons.folder"), "#334155", "#1E293B", self.open_output_folder)
        self.exit_button = self._make_action_button(actions_card, 10, self.t("buttons.exit"), "#7F1D1D", "#7C2D12", self._on_close)

    def _build_main_panel(self) -> None:
        header = ctk.CTkFrame(self.main_panel, fg_color=THEME["surface"], corner_radius=24)
        header.grid(row=0, column=0, sticky="ew", padx=18, pady=(18, 12))
        header.grid_columnconfigure(0, weight=1)
        header.grid_columnconfigure(1, weight=0)

        left = ctk.CTkFrame(header, fg_color="transparent")
        left.grid(row=0, column=0, sticky="ew", padx=18, pady=18)
        ctk.CTkLabel(
            left,
            text=self.t("header.title"),
            text_color=THEME["primary_text"],
            font=ctk.CTkFont("Segoe UI Variable Display", 34, weight="bold"),
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            left,
            text=self.t("header.subtitle"),
            wraplength=760,
            justify="left",
            text_color=THEME["muted_text"],
            font=ctk.CTkFont("Segoe UI", 14),
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))

        self.mode_segment = ctk.CTkSegmentedButton(
            header,
            values=["Solo guion", "Guion + prompts", "Proyecto completo"],
            variable=self.mode_var,
            selected_color=ui_color("#F97316", "#F97316"),
            selected_hover_color=ui_color("#EA580C", "#EA580C"),
            unselected_color=ui_color("#E2E8F0", "#1F2937"),
            unselected_hover_color=ui_color("#CBD5E1", "#334155"),
            text_color=THEME["primary_text"],
        )
        self.mode_segment.grid(row=0, column=1, padx=18, pady=18, sticky="e")

        status_strip = ctk.CTkFrame(self.main_panel, fg_color=THEME["status_bar"], corner_radius=22)
        status_strip.grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 12))
        status_strip.grid_columnconfigure(0, weight=0)
        status_strip.grid_columnconfigure(1, weight=0)
        status_strip.grid_columnconfigure(2, weight=1)
        status_strip.grid_columnconfigure(3, weight=0)
        status_strip.grid_rowconfigure(0, weight=0)
        status_strip.grid_rowconfigure(1, weight=0)
        self.connection_chip = ctk.CTkLabel(
            status_strip,
            text=self.t("status.testing_connection"),
            text_color=ui_color("#BFDBFE", "#7DD3FC"),
            font=ctk.CTkFont("Segoe UI", 14, weight="bold"),
        )
        self.connection_chip.grid(row=0, column=0, sticky="w", padx=18, pady=12)
        self.render_chip = ctk.CTkLabel(
            status_strip,
            text=self.t("status.render_chip", provider=self.video_provider_var.get()),
            text_color=ui_color("#A7F3D0", "#6EE7B7"),
            font=ctk.CTkFont("Segoe UI", 14, weight="bold"),
        )
        self.render_chip.grid(row=0, column=1, sticky="w", padx=(0, 18), pady=12)

        self.progress_bar = ctk.CTkProgressBar(status_strip, progress_color=ui_color("#F97316", "#FB923C"), fg_color=THEME["progress_bg"])
        self.progress_bar.grid(row=0, column=2, sticky="ew", padx=(0, 18), pady=12)
        self.progress_bar.set(0)
        self.progress_percent_label = ctk.CTkLabel(
            status_strip,
            text="0%",
            text_color=ui_color("#FDBA74", "#FDBA74"),
            font=ctk.CTkFont("Segoe UI", 13, weight="bold"),
        )
        self.progress_percent_label.grid(row=0, column=3, sticky="e", padx=(0, 18), pady=12)
        self.progress_detail_label = ctk.CTkLabel(
            status_strip,
            text=self.t("progress.detail", detail=self.t("progress.waiting_detail")),
            text_color=ui_color("#94A3B8", "#CBD5E1"),
            font=ctk.CTkFont("Segoe UI", 12),
            anchor="w",
            justify="left",
        )
        self.progress_detail_label.grid(row=1, column=0, columnspan=4, sticky="ew", padx=18, pady=(0, 10))

        self.tab_view = ctk.CTkTabview(
            self.main_panel,
            fg_color=THEME["surface"],
            segmented_button_selected_color=ui_color("#0F766E", "#0F766E"),
            segmented_button_selected_hover_color=ui_color("#115E59", "#134E4A"),
            segmented_button_unselected_color=ui_color("#E2E8F0", "#1F2937"),
            segmented_button_unselected_hover_color=ui_color("#CBD5E1", "#334155"),
            corner_radius=26,
        )
        self.tab_view.grid(row=2, column=0, sticky="nsew", padx=18, pady=(0, 18))
        self.main_panel.grid_rowconfigure(2, weight=1)

        self._tab_names = {
            "summary": self.t("tabs.summary"),
            "scenes": self.t("tabs.scenes"),
            "json": self.t("tabs.json"),
            "history": self.t("tabs.history"),
        }
        self.summary_tab = self.tab_view.add(self._tab_names["summary"])
        self.scenes_tab = self.tab_view.add(self._tab_names["scenes"])
        self.json_tab = self.tab_view.add(self._tab_names["json"])
        self.history_tab = self.tab_view.add(self._tab_names["history"])

        self.summary_text = self._make_output_textbox(self.summary_tab)
        self.scenes_text = self._make_output_textbox(self.scenes_tab)
        self.json_text = self._make_output_textbox(self.json_tab)

        self.history_tab.grid_columnconfigure(0, weight=1)
        self.history_tab.grid_rowconfigure(1, weight=1)
        history_header = ctk.CTkFrame(self.history_tab, fg_color="transparent")
        history_header.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))
        history_header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            history_header,
            text=self.t("history.saved_projects"),
            text_color=THEME["primary_text"],
            font=ctk.CTkFont("Segoe UI", 18, weight="bold"),
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(
            history_header,
            text=self.t("buttons.refresh"),
            command=self._load_history_buttons,
            fg_color=ui_color("#1D4ED8", "#2563EB"),
            hover_color=ui_color("#1E40AF", "#1D4ED8"),
            width=110,
        ).grid(row=0, column=1, sticky="e")
        self.history_scroll = ctk.CTkScrollableFrame(self.history_tab, fg_color=THEME["surface_alt"], corner_radius=18)
        self.history_scroll.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        self.history_scroll.grid_columnconfigure(0, weight=1)

    def _make_card(self, parent: ctk.CTkBaseClass, title: str, subtitle: str) -> ctk.CTkFrame:
        card = ctk.CTkFrame(parent, fg_color=THEME["card"], corner_radius=22)
        card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            card,
            text=title,
            text_color=THEME["hero_text"],
            font=ctk.CTkFont("Segoe UI", 18, weight="bold"),
        ).grid(row=0, column=0, sticky="w", padx=14, pady=(14, 4))
        ctk.CTkLabel(
            card,
            text=subtitle,
            text_color=ui_color("#94A3B8", "#94A3B8"),
            wraplength=330,
            justify="left",
            font=ctk.CTkFont("Segoe UI", 12),
        ).grid(row=1, column=0, sticky="w", padx=14, pady=(0, 8))
        return card

    def _make_labeled_entry(self, parent: ctk.CTkBaseClass, row: int, label: str, variable: tk.Variable) -> ctk.CTkEntry:
        label_widget = ctk.CTkLabel(parent, text=label, text_color=THEME["card_label"], font=ctk.CTkFont("Segoe UI", 13, weight="bold"))
        label_widget.grid(
            row=row,
            column=0,
            sticky="w",
            padx=14,
            pady=(8, 4),
        )
        entry = ctk.CTkEntry(parent, textvariable=variable, fg_color=THEME["input_bg"], text_color=THEME["primary_text"])
        entry.grid(row=row + 1, column=0, sticky="ew", padx=14, pady=(0, 8))
        entry._label_widget = label_widget  # type: ignore[attr-defined]
        return entry

    def _make_labeled_combo(self, parent: ctk.CTkBaseClass, row: int, label: str, variable: tk.StringVar, values: list[str]) -> ctk.CTkComboBox:
        ctk.CTkLabel(parent, text=label, text_color=THEME["card_label"], font=ctk.CTkFont("Segoe UI", 13, weight="bold")).grid(
            row=row,
            column=0,
            sticky="w",
            padx=14,
            pady=(8, 4),
        )
        combo = ctk.CTkComboBox(
            parent,
            variable=variable,
            values=values,
            fg_color=THEME["input_bg"],
            text_color=THEME["primary_text"],
            button_color=ui_color("#1D4ED8", "#2563EB"),
            button_hover_color=ui_color("#1E40AF", "#1D4ED8"),
        )
        combo.grid(row=row + 1, column=0, sticky="ew", padx=14, pady=(0, 8))
        return combo

    def _make_action_button(
        self,
        parent: ctk.CTkBaseClass,
        row: int,
        text: str,
        color: str,
        hover_color: str,
        command: Callable[[], None],
    ) -> ctk.CTkButton:
        button = ctk.CTkButton(parent, text=text, command=command, fg_color=color, hover_color=hover_color, height=38)
        button.grid(row=row, column=0, sticky="ew", padx=14, pady=(0, 8))
        return button

    def _make_output_textbox(self, tab: ctk.CTkBaseClass) -> ctk.CTkTextbox:
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=1)
        textbox = ctk.CTkTextbox(tab, fg_color=THEME["surface_alt"], text_color=THEME["primary_text"], border_width=1, border_color=THEME["surface_border"])
        textbox.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)
        textbox.insert("1.0", self.t("output.placeholder"))
        textbox.configure(state="disabled")
        return textbox

    def _on_temperature_change(self, value: float) -> None:
        self.temperature_value_label.configure(text=f"{value:.2f}")
        self._schedule_save()

    def _on_appearance_change(self) -> None:
        mode = self._appearance_label_to_mode(self.appearance_mode_var.get())
        self._apply_user_appearance_mode(mode)
        self._schedule_save()
        self._set_status(self.t("status.theme_updated", theme=self.appearance_mode_var.get()))

    def toggle_dark_mode(self) -> None:
        current = self._appearance_label_to_mode(self.appearance_mode_var.get())
        new_mode = "light" if current == "dark" else "dark"
        self.appearance_mode_var.set(self._appearance_mode_to_label(new_mode))
        self._on_appearance_change()

    def toggle_api_key_visibility(self) -> None:
        show = not self.show_api_key_var.get()
        self.show_api_key_var.set(show)
        self.api_key_entry.configure(show="" if show else "*")
        self.show_api_key_button.configure(text=self.t("buttons.hide") if show else self.t("buttons.show"))
        self._reset_auto_close_timer()

    def _on_video_provider_change(self) -> None:
        self._sync_video_provider_ui()
        self._schedule_save()
        provider = self.video_provider_var.get().strip() or "Storyboard local"
        message_key = {
            "Storyboard local": "status.provider_storyboard_note",
            "Local AI video": "status.provider_local_ai_note",
            "Local Avatar video": "status.provider_avatar_note",
        }.get(provider, "status.provider_updated")
        self._set_status(self.t(message_key, provider=provider), success=True)

    def _sync_video_format_preferences(self) -> None:
        desired_ratio = aspect_ratio_for_video_format(
            self.format_var.get().strip() or "YouTube Short",
            fallback=self.video_aspect_ratio_var.get().strip() or "9:16",
        )
        current_ratio = self.video_aspect_ratio_var.get().strip() or "9:16"
        if current_ratio != desired_ratio:
            self.video_aspect_ratio_var.set(desired_ratio)

    def _sync_video_provider_ui(self) -> None:
        provider = self.video_provider_var.get().strip() or "Storyboard local"
        self.render_chip.configure(text=self.t("status.render_chip", provider=provider))
        uses_comfyui_controls = provider in {"Storyboard local", "Local AI video", "Local Avatar video"}
        if hasattr(self, "local_ai_card"):
            if uses_comfyui_controls:
                self.local_ai_card.grid()
            else:
                self.local_ai_card.grid_remove()
        if hasattr(self, "local_video_button"):
            self.local_video_button.configure(state="normal" if uses_comfyui_controls and not self.is_busy else "disabled")
        self._sync_avatar_ui()
        self._sync_tts_ui()

    def _sync_avatar_ui(self) -> None:
        use_avatar = self.video_provider_var.get().strip() == "Local Avatar video"
        widgets = [
            getattr(self, "avatar_source_image_entry", None),
            getattr(self, "avatar_source_image_button", None),
        ]
        for widget in widgets:
            if widget is None:
                continue
            label_widget = getattr(widget, "_label_widget", None)
            if use_avatar:
                if label_widget is not None:
                    label_widget.grid()
                widget.grid()
            else:
                if label_widget is not None:
                    label_widget.grid_remove()
                widget.grid_remove()

    def _sync_tts_ui(self) -> None:
        use_piper = self.tts_backend_var.get().strip() == "Piper local"
        widgets = [
            getattr(self, "piper_executable_entry", None),
            getattr(self, "piper_model_entry", None),
            getattr(self, "piper_button", None),
        ]
        for widget in widgets:
            if widget is None:
                continue
            label_widget = getattr(widget, "_label_widget", None)
            if use_piper:
                if label_widget is not None:
                    label_widget.grid()
                widget.grid()
            else:
                if label_widget is not None:
                    label_widget.grid_remove()
                widget.grid_remove()

    def _report_callback_exception(self, exc: type[BaseException], value: BaseException, traceback_obj: Any) -> None:
        self.logger.exception("Unhandled UI exception", exc_info=(exc, value, traceback_obj))
        self._set_status(self.t("errors.unhandled", message=value), error=True)

    def _on_configure(self, event: tk.Event) -> None:
        if not self._closing and event.widget is self:
            if self._geometry_job_id:
                self.after_cancel(self._geometry_job_id)
            self._geometry_job_id = self.after(700, self._save_window_geometry)

    def _save_window_geometry(self) -> None:
        if self.state() != "zoomed":
            safe_geometry = sanitize_window_geometry(
                self.geometry(),
                fallback=self.config_manager.config.window_geometry,
            )
            self.config_manager.update(
                window_geometry=safe_geometry,
                window_zoomed=False,
            )
        else:
            self.config_manager.update(window_zoomed=True)

    def _schedule_save(self) -> None:
        if self._closing:
            return
        if self._save_job_id:
            self.after_cancel(self._save_job_id)
        self._save_job_id = self.after(250, self._save_gui_state)

    def _cancel_scheduled_jobs(self) -> None:
        for attribute in [
            "_save_job_id",
            "_geometry_job_id",
            "_countdown_job_id",
            "_process_queue_job_id",
            "_startup_show_job_id",
            "_startup_front_job_id",
            "_topmost_reset_job_id",
            "_inspect_env_job_id",
            "_load_models_job_id",
            "_auto_start_job_id",
            "_zoom_job_id",
            "_auto_close_trigger_job_id",
            "_render_capabilities_job_id",
            "_render_summary_job_id",
        ]:
            job_id = getattr(self, attribute)
            if job_id:
                try:
                    self.after_cancel(job_id)
                except tk.TclError:
                    pass
                setattr(self, attribute, None)

    def _save_gui_state(self) -> None:
        self._save_job_id = None
        appearance_mode = self._appearance_label_to_mode(self.appearance_mode_var.get())
        self._apply_user_appearance_mode(appearance_mode)
        self.config_manager.update(
            ui_language=self._selected_ui_language_code(),
            appearance_mode=appearance_mode,
            lmstudio_base_url=self.base_url_var.get().strip(),
            model=self.model_var.get().strip(),
            api_key=self.api_key_var.get(),
            video_provider=self.video_provider_var.get().strip() or "Storyboard local",
            video_aspect_ratio=self.video_aspect_ratio_var.get().strip() or "9:16",
            render_captions=bool(self.render_captions_var.get()),
            comfyui_base_url=self.comfyui_base_url_var.get().strip() or "http://127.0.0.1:8188",
            comfyui_worker_urls=self.comfyui_worker_urls_var.get().strip(),
            parallel_scene_workers=self._safe_positive_int(self.parallel_scene_workers_var.get(), 1),
            render_gpu_preference=self.render_gpu_var.get().strip() or "Auto",
            video_render_device_preference=self.video_render_device_var.get().strip() or "Auto",
            video_encoder_preference=self.video_encoder_var.get().strip() or "Auto",
            comfyui_checkpoint=self.comfyui_checkpoint_var.get().strip(),
            comfyui_workflow_path=self.comfyui_workflow_path_var.get().strip(),
            comfyui_negative_prompt=self.comfyui_negative_prompt_var.get().strip(),
            comfyui_poll_interval_seconds=self._safe_positive_int(self.comfyui_poll_interval_var.get(), 2),
            tts_backend=self.tts_backend_var.get().strip() or "Windows local",
            ffmpeg_path=self.ffmpeg_path_var.get().strip(),
            piper_executable_path=self.piper_executable_path_var.get().strip(),
            piper_model_path=self.piper_model_path_var.get().strip(),
            avatar_source_image_path=self.avatar_source_image_path_var.get().strip(),
            temperature=round(float(self.temperature_var.get()), 2),
            scene_count=self._safe_positive_int(self.scene_count_var.get(), self.app_config.scene_count),
            output_language=self.language_var.get().strip() or "Espanol",
            estimated_duration_seconds=self._safe_positive_int(self.duration_var.get(), self.app_config.estimated_duration_seconds),
            video_topic=self.topic_text.get("1.0", "end-1c").strip(),
            visual_style=self.visual_style_var.get().strip(),
            audience=self.audience_var.get().strip(),
            narrative_tone=self.tone_var.get().strip(),
            video_format=self.format_var.get().strip(),
            generation_mode=self.mode_var.get().strip(),
            output_dir=self.output_dir_var.get().strip() or "output",
            auto_start_enabled=bool(self.auto_start_var.get()),
            auto_close_enabled=bool(self.auto_close_var.get()),
            auto_close_seconds=self._safe_positive_int(self.auto_close_seconds_var.get(), 60),
            json_retry_attempts=self._safe_positive_int(self.retries_var.get(), 3),
            request_timeout_seconds=self._safe_positive_int(self.timeout_var.get(), 120),
            max_tokens=self._safe_positive_int(self.max_tokens_var.get(), 2800),
        )
        self.app_config = self.config_manager.config
        self._auto_close_remaining = max(1, int(self.app_config.auto_close_seconds))
        self._sync_video_provider_ui()
        self._update_countdown_label()

    def _safe_positive_int(self, value: str, fallback: int) -> int:
        try:
            parsed = int(value)
            return parsed if parsed > 0 else fallback
        except (TypeError, ValueError):
            return fallback

    def _build_client(self) -> LMStudioClient:
        return LMStudioClient(
            base_url=self.base_url_var.get().strip(),
            api_key=self.api_key_var.get(),
            timeout_seconds=self._safe_positive_int(self.timeout_var.get(), 120),
        )

    def _build_local_video_client(self) -> ComfyUIClient:
        return ComfyUIClient(
            base_url=self.comfyui_base_url_var.get().strip() or "http://127.0.0.1:8188",
            timeout_seconds=self._safe_positive_int(self.timeout_var.get(), 180),
        )

    def _build_request(self) -> GenerationRequest:
        topic = self.topic_text.get("1.0", "end-1c").strip()
        if not topic:
            raise ValueError(self.t("errors.enter_topic"))
        return GenerationRequest(
            topic=topic,
            visual_style=self.visual_style_var.get().strip() or "Cinematic",
            audience=self.audience_var.get().strip() or "General",
            narrative_tone=self.tone_var.get().strip() or "Cinematic",
            video_format=self.format_var.get().strip() or "YouTube Short",
            output_language=self.language_var.get().strip() or "Espanol",
            total_duration_seconds=self._safe_positive_int(self.duration_var.get(), 60),
            scene_count=self._safe_positive_int(self.scene_count_var.get(), 6),
            generation_mode=self.mode_var.get().strip() or "Proyecto completo",
            model=self.model_var.get().strip(),
            temperature=float(self.temperature_var.get()),
            max_tokens=self._safe_positive_int(self.max_tokens_var.get(), 2800),
        )

    def _project_requests_silent_narration(self, project: VideoProject | None = None) -> bool:
        if project is not None and project.source_topic.strip():
            return brief_requests_silent_narration(project.source_topic)
        if hasattr(self, "topic_text"):
            return brief_requests_silent_narration(self.topic_text.get("1.0", "end-1c"))
        return False

    def _apply_silent_narration_override(self, project: VideoProject | None = None, provider: str | None = None) -> bool:
        active_provider = (provider or self.video_provider_var.get().strip() or "Storyboard local").strip()
        if active_provider == "Local Avatar video":
            return False
        if not self._project_requests_silent_narration(project):
            return False
        changed = False
        if self.tts_backend_var.get().strip() != "Sin voz":
            self.tts_backend_var.set("Sin voz")
            changed = True
        if bool(self.render_captions_var.get()):
            self.render_captions_var.set(False)
            changed = True
        if changed:
            self._schedule_save()
        self._set_status(self.t("status.silent_brief_forcing_no_voice"), success=True)
        return True

    def _build_video_render_request(self) -> VideoRenderRequest:
        if not self.current_project:
            raise ValueError("Generate or load a project before creating the final video.")
        return self._build_video_render_request_for_project(self.current_project)

    def _describe_workflow_mode(self, workflow_path: str) -> str:
        if not workflow_path.strip():
            return "missing"
        return detect_workflow_output_mode(workflow_path)

    def _capture_video_render_settings(self) -> dict[str, Any]:
        provider = self.video_provider_var.get().strip() or "Storyboard local"
        aspect_ratio = aspect_ratio_for_video_format(
            self.format_var.get().strip() or "YouTube Short",
            fallback=self.video_aspect_ratio_var.get().strip() or "9:16",
        )
        if self.video_aspect_ratio_var.get().strip() != aspect_ratio:
            self.video_aspect_ratio_var.set(aspect_ratio)
        workflow_path = self.comfyui_workflow_path_var.get().strip()
        checkpoint = self.comfyui_checkpoint_var.get().strip()
        workflow_mode = self._describe_workflow_mode(workflow_path)
        avatar_source_image_path = self.avatar_source_image_path_var.get().strip()
        if provider == "Local AI video":
            if workflow_mode == "missing":
                raise ValueError(self.t("errors.workflow_required"))
            if workflow_mode == "image":
                raise ValueError(self.t("errors.workflow_image_only"))
        if provider == "Local Avatar video":
            if not avatar_source_image_path:
                raise ValueError(self.t("errors.avatar_image_missing"))
            avatar_path = Path(avatar_source_image_path).expanduser()
            if not avatar_path.exists():
                raise FileNotFoundError(self.t("errors.avatar_image_not_found", path=avatar_path))
            if (self.tts_backend_var.get().strip() or "Windows local") == "Sin voz":
                raise ValueError(self.t("errors.avatar_audio_required"))
        ffmpeg_path = self.ffmpeg_path_var.get().strip() or self.setup_manager.resolve_ffmpeg_path(self.ffmpeg_path_var.get())
        if ffmpeg_path and ffmpeg_path != self.ffmpeg_path_var.get().strip():
            self.ffmpeg_path_var.set(ffmpeg_path)
        return {
            "output_dir": self.config_manager.resolve_output_dir(),
            "provider": provider,
            "aspect_ratio": aspect_ratio,
            "request_timeout_seconds": self._safe_positive_int(self.timeout_var.get(), 180),
            "render_captions": bool(self.render_captions_var.get()),
            "comfyui_base_url": self.comfyui_base_url_var.get().strip() or "http://127.0.0.1:8188",
            "comfyui_worker_urls": self.comfyui_worker_urls_var.get().strip(),
            "parallel_scene_workers": self._safe_positive_int(self.parallel_scene_workers_var.get(), 1),
            "render_gpu_preference": self.render_gpu_var.get().strip() or "Auto",
            "video_render_device_preference": self.video_render_device_var.get().strip() or "Auto",
            "video_encoder_preference": self.video_encoder_var.get().strip() or "Auto",
            "comfyui_checkpoint": checkpoint,
            "comfyui_workflow_path": workflow_path,
            "comfyui_negative_prompt": self.comfyui_negative_prompt_var.get().strip(),
            "comfyui_poll_interval_seconds": self._safe_positive_int(self.comfyui_poll_interval_var.get(), 2),
            "tts_backend": self.tts_backend_var.get().strip() or "Windows local",
            "ffmpeg_path": ffmpeg_path,
            "piper_executable_path": self.piper_executable_path_var.get().strip(),
            "piper_model_path": self.piper_model_path_var.get().strip(),
            "avatar_source_image_path": avatar_source_image_path,
        }

    def _build_video_render_request_for_project(self, project: VideoProject, settings: dict[str, Any] | None = None) -> VideoRenderRequest:
        options = settings or self._capture_video_render_settings()
        effective_tts_backend = options["tts_backend"]
        effective_render_captions = options["render_captions"]
        if self._project_requests_silent_narration(project) and options["provider"] != "Local Avatar video":
            effective_tts_backend = "Sin voz"
            effective_render_captions = False
        return VideoRenderRequest(
            project=project,
            output_dir=options["output_dir"],
            provider=options["provider"],
            aspect_ratio=options["aspect_ratio"],
            request_timeout_seconds=options["request_timeout_seconds"],
            render_captions=effective_render_captions,
            comfyui_base_url=options["comfyui_base_url"],
            comfyui_worker_urls=options["comfyui_worker_urls"],
            parallel_scene_workers=options["parallel_scene_workers"],
            render_gpu_preference=options["render_gpu_preference"],
            video_render_device_preference=options["video_render_device_preference"],
            video_encoder_preference=options["video_encoder_preference"],
            comfyui_checkpoint=options["comfyui_checkpoint"],
            comfyui_workflow_path=options["comfyui_workflow_path"],
            comfyui_negative_prompt=options["comfyui_negative_prompt"],
            comfyui_poll_interval_seconds=options["comfyui_poll_interval_seconds"],
            tts_backend=effective_tts_backend,
            ffmpeg_path=options["ffmpeg_path"],
            piper_executable_path=options["piper_executable_path"],
            piper_model_path=options["piper_model_path"],
            avatar_source_image_path=options["avatar_source_image_path"],
        )

    def _build_runtime_config_snapshot(self, render_settings: dict[str, Any]) -> AppConfig:
        return replace(
            self.app_config,
            ui_language=self._selected_ui_language_code(),
            lmstudio_base_url=self.base_url_var.get().strip() or "http://127.0.0.1:1234",
            model=self.model_var.get().strip(),
            video_provider=render_settings["provider"],
            video_aspect_ratio=render_settings["aspect_ratio"],
            render_captions=bool(render_settings["render_captions"]),
            comfyui_base_url=render_settings["comfyui_base_url"],
            comfyui_worker_urls=render_settings["comfyui_worker_urls"],
            parallel_scene_workers=self._safe_positive_int(str(render_settings["parallel_scene_workers"]), 1),
            render_gpu_preference=str(render_settings["render_gpu_preference"] or "Auto"),
            video_render_device_preference=str(render_settings["video_render_device_preference"] or "Auto"),
            video_encoder_preference=str(render_settings["video_encoder_preference"] or "Auto"),
            comfyui_checkpoint=render_settings["comfyui_checkpoint"],
            comfyui_workflow_path=render_settings["comfyui_workflow_path"],
            comfyui_negative_prompt=render_settings["comfyui_negative_prompt"],
            comfyui_poll_interval_seconds=self._safe_positive_int(str(render_settings["comfyui_poll_interval_seconds"]), 2),
            tts_backend=render_settings["tts_backend"],
            ffmpeg_path=render_settings["ffmpeg_path"],
            piper_executable_path=render_settings["piper_executable_path"],
            piper_model_path=render_settings["piper_model_path"],
            avatar_source_image_path=render_settings["avatar_source_image_path"],
            request_timeout_seconds=self._safe_positive_int(self.timeout_var.get(), 120),
        )

    def _build_environment_config_snapshot(self) -> AppConfig:
        return replace(
            self.app_config,
            ui_language=self._selected_ui_language_code(),
            appearance_mode=self._appearance_label_to_mode(self.appearance_mode_var.get()),
            lmstudio_base_url=self.base_url_var.get().strip() or "http://127.0.0.1:1234",
            model=self.model_var.get().strip(),
            video_provider=self.video_provider_var.get().strip() or "Storyboard local",
            video_aspect_ratio=self.video_aspect_ratio_var.get().strip() or "9:16",
            render_captions=bool(self.render_captions_var.get()),
            comfyui_base_url=self.comfyui_base_url_var.get().strip() or "http://127.0.0.1:8188",
            comfyui_worker_urls=self.comfyui_worker_urls_var.get().strip(),
            parallel_scene_workers=self._safe_positive_int(self.parallel_scene_workers_var.get(), 1),
            render_gpu_preference=self.render_gpu_var.get().strip() or "Auto",
            video_render_device_preference=self.video_render_device_var.get().strip() or "Auto",
            video_encoder_preference=self.video_encoder_var.get().strip() or "Auto",
            comfyui_checkpoint=self.comfyui_checkpoint_var.get().strip(),
            comfyui_workflow_path=self.comfyui_workflow_path_var.get().strip(),
            comfyui_negative_prompt=self.comfyui_negative_prompt_var.get().strip(),
            comfyui_poll_interval_seconds=self._safe_positive_int(self.comfyui_poll_interval_var.get(), 2),
            tts_backend=self.tts_backend_var.get().strip() or "Windows local",
            ffmpeg_path=self.ffmpeg_path_var.get().strip(),
            piper_executable_path=self.piper_executable_path_var.get().strip(),
            piper_model_path=self.piper_model_path_var.get().strip(),
            avatar_source_image_path=self.avatar_source_image_path_var.get().strip(),
            request_timeout_seconds=self._safe_positive_int(self.timeout_var.get(), 120),
        )

    def _persist_runtime_updates(
        self,
        render_settings: dict[str, Any],
        updates: dict[str, Any],
        *,
        message: str,
        checkpoints: list[str] | None = None,
    ) -> None:
        if not updates:
            return
        self.config_manager.update(**updates)
        self.app_config = self.config_manager.config
        key_map = {
            "video_provider": "provider",
            "ffmpeg_path": "ffmpeg_path",
            "comfyui_base_url": "comfyui_base_url",
            "comfyui_worker_urls": "comfyui_worker_urls",
            "parallel_scene_workers": "parallel_scene_workers",
            "render_gpu_preference": "render_gpu_preference",
            "video_render_device_preference": "video_render_device_preference",
            "video_encoder_preference": "video_encoder_preference",
            "comfyui_checkpoint": "comfyui_checkpoint",
            "comfyui_workflow_path": "comfyui_workflow_path",
            "comfyui_negative_prompt": "comfyui_negative_prompt",
            "comfyui_poll_interval_seconds": "comfyui_poll_interval_seconds",
            "tts_backend": "tts_backend",
            "piper_executable_path": "piper_executable_path",
            "piper_model_path": "piper_model_path",
            "avatar_source_image_path": "avatar_source_image_path",
        }
        for config_key, render_key in key_map.items():
            if config_key in updates:
                render_settings[render_key] = updates[config_key]
        self._queue_event(
            "environment",
            summary="",
            checkpoints=checkpoints or [],
            updates=updates,
            message=message,
            success=True,
        )

    def _ensure_lmstudio_ready_for_generation(self, request: GenerationRequest) -> tuple[LMStudioClient, bool, list[str]]:
        base_url = self.base_url_var.get().strip() or "http://127.0.0.1:1234"
        client = LMStudioClient(
            base_url=base_url,
            api_key=self.api_key_var.get(),
            timeout_seconds=self._safe_positive_int(self.timeout_var.get(), 120),
        )
        success, models, message = self.setup_manager.wait_for_lmstudio(base_url, timeout_seconds=8)
        if not success:
            self.setup_manager.ensure_package_installed(LM_STUDIO_PACKAGE_ID, install_missing=True)
            if self.setup_manager.launch_application("lmstudio"):
                self._queue_event("progress", value=0.06, message=self.t("progress.opening_lmstudio_auto"))
                success, models, message = self.setup_manager.wait_for_lmstudio(base_url, timeout_seconds=90)
        if success:
            self._queue_event("connection", models=models, message=message)
            if not request.model and models:
                request.model = models[0]
        return client, success, models

    def _prepare_render_settings_for_full_video(self, render_settings: dict[str, Any]) -> dict[str, Any]:
        ffmpeg_path = self.setup_manager.ensure_ffmpeg_ready(render_settings["ffmpeg_path"], install_missing=True)
        if ffmpeg_path:
            self._persist_runtime_updates(
                render_settings,
                {"ffmpeg_path": ffmpeg_path},
                message=self.t("status.ffmpeg_prepared"),
            )

        if render_settings["provider"] == "Storyboard local":
            config_snapshot = self._build_runtime_config_snapshot(render_settings)
            prep_result = self.setup_manager.prepare_environment(
                config_snapshot,
                install_missing=True,
                install_default_checkpoint=True,
                progress_callback=lambda value, message: self._queue_event(
                    "progress",
                    value=0.03 + (value * 0.15),
                    message=message,
                ),
            )
            updates = dict(prep_result.updates)
            if ffmpeg_path:
                updates["ffmpeg_path"] = ffmpeg_path

            checkpoints = list(prep_result.status.checkpoints)
            checkpoint_name = str(updates.get("comfyui_checkpoint") or render_settings["comfyui_checkpoint"]).strip()
            workflow_path = str(updates.get("comfyui_workflow_path") or render_settings["comfyui_workflow_path"]).strip()
            workflow_mode = self._describe_workflow_mode(workflow_path) if workflow_path else "missing"
            if checkpoint_name and workflow_mode != "image":
                updates["comfyui_workflow_path"] = str(
                    self.setup_manager.ensure_default_workflow(
                        checkpoint_name=checkpoint_name,
                        aspect_ratio=str(render_settings["aspect_ratio"]),
                    )
                )

            storyboard_ai_ready = prep_result.status.comfyui_reachable
            if not storyboard_ai_ready:
                self.setup_manager.ensure_package_installed(COMFYUI_PACKAGE_ID, install_missing=True)
                if self.setup_manager.launch_application(
                    "comfyui",
                    gpu_choice=str(render_settings["render_gpu_preference"] or "Auto"),
                    configured_url=str(updates.get("comfyui_base_url") or render_settings["comfyui_base_url"]),
                ):
                    self._queue_event("progress", value=0.19, message=self.t("progress.opening_comfyui_storyboard"))
                ready, resolved_url, detected_checkpoints, _message = self.setup_manager.wait_for_comfyui(
                    str(updates.get("comfyui_base_url") or render_settings["comfyui_base_url"]),
                    timeout_seconds=120,
                    require_checkpoints=True,
                )
                if ready:
                    storyboard_ai_ready = True
                    checkpoints = detected_checkpoints
                    updates["comfyui_base_url"] = resolved_url
                    worker_urls = self.setup_manager.resolve_comfyui_worker_urls(
                        str(updates.get("comfyui_worker_urls") or render_settings["comfyui_worker_urls"]),
                        resolved_url,
                    )
                    updates["comfyui_worker_urls"] = ", ".join(worker_urls)
                    updates["parallel_scene_workers"] = max(1, len(worker_urls))
                    if detected_checkpoints and not checkpoint_name:
                        checkpoint_name = detected_checkpoints[0]
                        updates["comfyui_checkpoint"] = checkpoint_name
                    if checkpoint_name:
                        updates["comfyui_workflow_path"] = str(
                            self.setup_manager.ensure_default_workflow(
                                checkpoint_name=checkpoint_name,
                                aspect_ratio=str(render_settings["aspect_ratio"]),
                            )
                        )

            self._persist_runtime_updates(
                render_settings,
                updates,
                message=(
                    self.t("status.storyboard_environment_prepared")
                    if storyboard_ai_ready
                    else self.t("status.storyboard_local_fallback")
                ),
                checkpoints=checkpoints,
            )
            return render_settings

        if render_settings["provider"] == "Local Avatar video":
            self.setup_manager.download_default_avatar_vae(
                progress_callback=lambda value, message: self._queue_event(
                    "progress",
                    value=0.03 + (value * 0.08),
                    message=message,
                ),
            )
            config_snapshot = self._build_runtime_config_snapshot(render_settings)
            status = self.setup_manager.inspect_environment(config_snapshot)
            updates: dict[str, Any] = {
                "ffmpeg_path": ffmpeg_path or render_settings["ffmpeg_path"],
                "comfyui_base_url": status.comfyui_base_url or render_settings["comfyui_base_url"],
                "comfyui_worker_urls": ", ".join(status.comfyui_worker_urls),
                "parallel_scene_workers": max(1, len(status.comfyui_worker_urls)),
                "avatar_source_image_path": render_settings["avatar_source_image_path"],
            }
            local_avatar_endpoint = (
                status.comfyui_worker_urls[0]
                if status.comfyui_worker_urls
                else str(status.comfyui_base_url or render_settings["comfyui_base_url"]).strip().rstrip("/")
            )
            workflow_path = str(render_settings["comfyui_workflow_path"]).strip()
            workflow_mode = self._describe_workflow_mode(workflow_path) if workflow_path else "missing"
            if workflow_mode != "video":
                updates["comfyui_workflow_path"] = str(
                    self.setup_manager.ensure_default_avatar_workflow(
                        aspect_ratio=str(render_settings["aspect_ratio"]),
                    )
                )
            local_avatar_ready = bool(local_avatar_endpoint) and (
                status.comfyui_reachable or bool(status.comfyui_worker_urls)
            )
            if not local_avatar_ready:
                self.setup_manager.ensure_package_installed(COMFYUI_PACKAGE_ID, install_missing=True)
                if self.setup_manager.launch_application(
                    "comfyui",
                    gpu_choice=str(render_settings["render_gpu_preference"] or "Auto"),
                    configured_url=str(updates.get("comfyui_base_url") or render_settings["comfyui_base_url"]),
                ):
                    self._queue_event("progress", value=0.18, message=self.t("progress.opening_comfyui_avatar"))
                ready, resolved_url, _checkpoints, _message = self.setup_manager.wait_for_comfyui(
                    str(updates.get("comfyui_base_url") or render_settings["comfyui_base_url"]),
                    timeout_seconds=120,
                    require_checkpoints=False,
                )
                worker_urls = self.setup_manager.resolve_comfyui_worker_urls(
                    str(updates.get("comfyui_worker_urls") or render_settings["comfyui_worker_urls"]),
                    resolved_url,
                )
                if ready or worker_urls:
                    local_avatar_ready = True
                    updates["comfyui_base_url"] = resolved_url
                    updates["comfyui_worker_urls"] = ", ".join(worker_urls)
                    updates["parallel_scene_workers"] = max(1, len(worker_urls))
                    local_avatar_endpoint = (
                        worker_urls[0]
                        if worker_urls
                        else str(resolved_url or render_settings["comfyui_base_url"]).strip().rstrip("/")
                    )
            if not local_avatar_ready:
                raise LocalAIVideoWorkflowError(
                    self.t(
                        "errors.avatar_comfyui_not_ready",
                        url=local_avatar_endpoint or str(render_settings["comfyui_base_url"]).strip() or "http://127.0.0.1:8188",
                    )
                )
            avatar_nodes_ready, missing_avatar_nodes = self.setup_manager.comfyui_has_nodes(
                local_avatar_endpoint,
                ["Echo_LoadModel", "Echo_Predata", "Echo_Sampler", "VHS_LoadAudio", "VHS_LoadImagePath", "VHS_VideoCombine"],
            )
            if not avatar_nodes_ready:
                raise LocalAIVideoWorkflowError(
                    self.t("errors.avatar_nodes_missing", nodes=", ".join(missing_avatar_nodes))
                )
            self._persist_runtime_updates(
                render_settings,
                updates,
                message=self.t("status.avatar_environment_prepared"),
                checkpoints=status.checkpoints,
            )
            return render_settings

        config_snapshot = self._build_runtime_config_snapshot(render_settings)
        prep_result = self.setup_manager.prepare_environment(
            config_snapshot,
            install_missing=True,
            install_default_checkpoint=True,
            progress_callback=lambda value, message: self._queue_event(
                "progress",
                value=0.03 + (value * 0.17),
                message=message,
            ),
        )
        updates = dict(prep_result.updates)
        if ffmpeg_path:
            updates["ffmpeg_path"] = ffmpeg_path
        workflow_path = str(updates.get("comfyui_workflow_path") or render_settings["comfyui_workflow_path"]).strip()
        if workflow_path and self._describe_workflow_mode(workflow_path) == "image":
            raise LocalAIVideoWorkflowError(
                self.t("errors.image_workflow_only_auto")
            )

        checkpoints = list(prep_result.status.checkpoints)
        local_ai_ready = prep_result.status.comfyui_reachable and bool(checkpoints or updates.get("comfyui_checkpoint"))

        if not local_ai_ready:
            self.setup_manager.ensure_package_installed(COMFYUI_PACKAGE_ID, install_missing=True)
            if self.setup_manager.launch_application(
                "comfyui",
                gpu_choice=str(render_settings["render_gpu_preference"] or "Auto"),
                configured_url=str(updates.get("comfyui_base_url") or render_settings["comfyui_base_url"]),
            ):
                self._queue_event("progress", value=0.19, message=self.t("progress.opening_comfyui_auto"))
            ready, resolved_url, detected_checkpoints, _message = self.setup_manager.wait_for_comfyui(
                str(updates.get("comfyui_base_url") or render_settings["comfyui_base_url"]),
                timeout_seconds=120,
                require_checkpoints=True,
            )
            if ready:
                local_ai_ready = True
                checkpoints = detected_checkpoints
                updates["comfyui_base_url"] = resolved_url
                worker_urls = self.setup_manager.resolve_comfyui_worker_urls(
                    str(updates.get("comfyui_worker_urls") or render_settings["comfyui_worker_urls"]),
                    resolved_url,
                )
                updates["comfyui_worker_urls"] = ", ".join(worker_urls)
                updates["parallel_scene_workers"] = max(1, len(worker_urls))
                if checkpoints and not updates.get("comfyui_checkpoint"):
                    updates["comfyui_checkpoint"] = checkpoints[0]
                if updates.get("comfyui_checkpoint"):
                    updates["comfyui_workflow_path"] = str(
                        self.setup_manager.ensure_default_workflow(
                            checkpoint_name=str(updates["comfyui_checkpoint"]),
                            aspect_ratio=str(render_settings["aspect_ratio"]),
                        )
                    )

        workflow_path = str(updates.get("comfyui_workflow_path") or render_settings["comfyui_workflow_path"]).strip()
        if workflow_path and self._describe_workflow_mode(workflow_path) == "image":
            raise LocalAIVideoWorkflowError(
                self.t("errors.image_workflow_only_selected")
            )
        if not local_ai_ready or not workflow_path:
            updates["video_provider"] = "Storyboard local"
            self._persist_runtime_updates(
                render_settings,
                updates,
                message=self.t("status.storyboard_auto_fallback_selected"),
                checkpoints=checkpoints,
            )
            return render_settings

        self._persist_runtime_updates(
            render_settings,
            updates,
            message=self.t("status.full_video_environment_prepared"),
            checkpoints=checkpoints,
        )
        return render_settings

    def _queue_event(self, event_type: str, **payload: Any) -> None:
        self.task_queue.put({"type": event_type, **payload})

    def _populate_detected_gpu_options(self) -> None:
        self._schedule_render_capability_refresh(immediate=True)

    def _schedule_render_capability_refresh(self, *, immediate: bool = False) -> None:
        if self._closing:
            return
        if self._render_capabilities_job_id:
            self.after_cancel(self._render_capabilities_job_id)
        delay_ms = 0 if immediate else 220
        self._render_capabilities_job_id = self.after(delay_ms, self._refresh_render_capabilities_async)

    def _refresh_render_capabilities_async(self) -> None:
        self._render_capabilities_job_id = None
        ffmpeg_path = self.ffmpeg_path_var.get().strip()

        def worker() -> None:
            try:
                detection = self.gpu_detector.detect(ffmpeg_path)
            except Exception as exc:
                self._queue_event("render_capabilities_error", message=str(exc))
                return
            self._queue_event("render_capabilities", detection=detection)

        threading.Thread(target=worker, daemon=True, name="videogenius-render-capabilities").start()

    def _apply_render_capabilities(self, detection: GPUDetectionResult) -> None:
        self._last_gpu_detection = detection
        self._apply_gpu_options(
            [device.name for device in detection.devices],
            list(detection.encoder_options),
        )
        self._update_render_selection_summary()

    def _apply_gpu_options(self, gpu_names: list[str], encoder_options: list[str] | None = None) -> None:
        if hasattr(self, "render_gpu_combo"):
            local_ai_options = self.setup_manager.format_gpu_options(gpu_names)
            current_local_ai = self.render_gpu_var.get().strip() or "Auto"
            self.render_gpu_combo.configure(values=local_ai_options)
            if current_local_ai not in local_ai_options:
                self.render_gpu_var.set("Auto")
        if hasattr(self, "video_render_device_combo"):
            render_options = self.setup_manager.format_video_render_options(gpu_names)
            current_render = self.video_render_device_var.get().strip() or "Auto"
            self.video_render_device_combo.configure(values=render_options)
            if current_render not in render_options:
                self.video_render_device_var.set("Auto")
        if hasattr(self, "video_encoder_combo"):
            current_encoder = self.video_encoder_var.get().strip() or "Auto"
            available_encoders = encoder_options or ["Auto", "libx264"]
            self.video_encoder_combo.configure(values=available_encoders)
            if current_encoder not in available_encoders:
                self.video_encoder_var.set("Auto")

    def _schedule_render_selection_summary_update(self) -> None:
        if self._closing:
            return
        if self._render_summary_job_id:
            self.after_cancel(self._render_summary_job_id)
        self._render_summary_job_id = self.after(50, self._update_render_selection_summary)

    def _format_detected_gpu_summary(self, detection: GPUDetectionResult | None) -> str:
        if detection is None or not detection.devices:
            return self.t("local_ai.no_gpu_detected")
        labels = [f"GPU {device.index}: {device.name}" for device in detection.devices]
        if len(labels) > 3:
            labels = [*labels[:3], f"+{len(detection.devices) - 3}"]
        return self.t("local_ai.detected_gpus", gpus=" | ".join(labels))

    def _update_render_selection_summary(self) -> None:
        self._render_summary_job_id = None
        if hasattr(self, "detected_gpus_summary_label"):
            self.detected_gpus_summary_label.configure(text=self._format_detected_gpu_summary(self._last_gpu_detection))
        if not hasattr(self, "active_encoder_summary_label"):
            return
        if self._last_gpu_detection is None:
            self.active_encoder_summary_label.configure(text=self.t("local_ai.active_encoder_pending"))
            return
        try:
            selection = describe_render_selection(
                self.video_render_device_var.get().strip() or "Auto",
                list(self._last_gpu_detection.devices),
                ffmpeg_path=self.ffmpeg_path_var.get().strip(),
                available_encoders=set(self._last_gpu_detection.ffmpeg_encoders),
                encoder_preference=self.video_encoder_var.get().strip() or "Auto",
            )
            plan = selection.selected_plan
            self.active_encoder_summary_label.configure(
                text=self.t("local_ai.active_encoder", encoder=plan.encoder_name, device=plan.label)
            )
        except Exception as exc:
            self.logger.warning("Unable to summarize render selection: %s", exc)
            self.active_encoder_summary_label.configure(text=self.t("local_ai.active_encoder_pending"))

    def _run_in_background(self, label: str, worker: Callable[[], None]) -> None:
        if self.is_busy:
            self._set_status(self.t("errors.task_running"), error=True)
            return
        self.is_busy = True
        self._toggle_busy_state(True)
        self._set_status(f"{label}...")
        self._set_progress_ui(0.02, label)
        started_at = time.perf_counter()
        self.logger.info("Background task started | task=%s", label)

        def runner() -> None:
            try:
                worker()
            except Exception as exc:
                elapsed_ms = int((time.perf_counter() - started_at) * 1000)
                self.logger.exception("Background task failed | task=%s | duration_ms=%s", label, elapsed_ms)
                self._queue_event("error", message=str(exc))
            else:
                elapsed_ms = int((time.perf_counter() - started_at) * 1000)
                self.logger.info("Background task finished | task=%s | duration_ms=%s", label, elapsed_ms)
            finally:
                self._queue_event("done")

        thread_name = f"videogenius-{label.lower().replace(' ', '-')[:24]}"
        threading.Thread(target=runner, daemon=True, name=thread_name).start()

    def _process_task_queue(self) -> None:
        try:
            while True:
                event = self.task_queue.get_nowait()
                event_type = event.get("type")
                # Tk widgets must be updated on the main thread, so workers push events into this queue.
                if event_type == "progress":
                    value = float(event.get("value", 0))
                    message = str(event.get("message", ""))
                    self._set_progress_ui(value, message)
                    self._set_status(message)
                elif event_type == "error":
                    self._set_progress_ui(0, self.t("progress.error_detail"))
                    self._set_status(str(event.get("message", "Unknown error")), error=True)
                elif event_type == "connection":
                    models = event.get("models", [])
                    message = str(event.get("message", ""))
                    self.connection_chip.configure(text=self.t("status.connection_chip", message=message))
                    if models:
                        self.model_combo.configure(values=models)
                        if not self.model_var.get().strip():
                            self.model_var.set(models[0])
                    self._set_status(message)
                elif event_type == "local_video_connection":
                    message = str(event.get("message", ""))
                    checkpoints = event.get("checkpoints", [])
                    if checkpoints and hasattr(self, "comfyui_checkpoint_combo"):
                        self.comfyui_checkpoint_combo.configure(values=checkpoints)
                        if not self.comfyui_checkpoint_var.get().strip():
                            self.comfyui_checkpoint_var.set(checkpoints[0])
                    self.render_chip.configure(text=self.t("status.local_connection_chip", message=message))
                    self._set_status(message)
                elif event_type == "environment":
                    status = event.get("status")
                    summary = str(event.get("summary", ""))
                    if isinstance(status, SetupStatus):
                        summary = self._format_setup_summary(status)
                    checkpoint_values = event.get("checkpoints", [])
                    updates = event.get("updates", {})
                    if isinstance(updates, dict):
                        if "comfyui_base_url" in updates:
                            self.comfyui_base_url_var.set(str(updates.get("comfyui_base_url") or ""))
                        if "comfyui_worker_urls" in updates:
                            self.comfyui_worker_urls_var.set(str(updates.get("comfyui_worker_urls") or ""))
                        if "parallel_scene_workers" in updates:
                            self.parallel_scene_workers_var.set(str(updates.get("parallel_scene_workers") or "1"))
                        if "render_gpu_preference" in updates:
                            self.render_gpu_var.set(str(updates.get("render_gpu_preference") or "Auto"))
                        if "video_render_device_preference" in updates:
                            self.video_render_device_var.set(str(updates.get("video_render_device_preference") or "Auto"))
                        if "video_encoder_preference" in updates:
                            self.video_encoder_var.set(str(updates.get("video_encoder_preference") or "Auto"))
                        if "ffmpeg_path" in updates:
                            self.ffmpeg_path_var.set(str(updates.get("ffmpeg_path") or ""))
                        if "comfyui_checkpoint" in updates:
                            self.comfyui_checkpoint_var.set(str(updates.get("comfyui_checkpoint") or ""))
                        if "comfyui_workflow_path" in updates:
                            self.comfyui_workflow_path_var.set(str(updates.get("comfyui_workflow_path") or ""))
                        if "avatar_source_image_path" in updates:
                            self.avatar_source_image_path_var.set(str(updates.get("avatar_source_image_path") or ""))
                        if "video_provider" in updates:
                            self.video_provider_var.set(str(updates.get("video_provider") or "Storyboard local"))
                        if "tts_backend" in updates:
                            self.tts_backend_var.set(str(updates.get("tts_backend") or "Windows local"))
                        if "comfyui_negative_prompt" in updates and not self.comfyui_negative_prompt_var.get().strip():
                            self.comfyui_negative_prompt_var.set(str(updates.get("comfyui_negative_prompt") or ""))
                    if checkpoint_values and hasattr(self, "comfyui_checkpoint_combo"):
                        self.comfyui_checkpoint_combo.configure(values=checkpoint_values)
                    gpu_names = event.get("gpu_names", [])
                    if isinstance(gpu_names, list):
                        self._apply_gpu_options([str(item) for item in gpu_names if str(item).strip()])
                    self._schedule_render_capability_refresh()
                    if summary and hasattr(self, "setup_summary_label"):
                        self.setup_summary_label.configure(text=summary)
                    self._sync_video_provider_ui()
                    self._set_status(str(event.get("message", summary or self.t("status.environment_updated"))), success=bool(event.get("success", False)))
                elif event_type == "render_capabilities":
                    detection = event.get("detection")
                    if isinstance(detection, GPUDetectionResult):
                        self._apply_render_capabilities(detection)
                elif event_type == "render_capabilities_error":
                    self.logger.warning("Unable to refresh render capabilities: %s", event.get("message", "unknown error"))
                    self._last_gpu_detection = None
                    self._apply_gpu_options([])
                    self._update_render_selection_summary()
                elif event_type == "project":
                    self.current_project = event["project"]
                    self.current_history_path = event.get("history_path")
                    self._render_project(self.current_project)
                    self._load_history_buttons()
                    if bool(event.get("finished", True)):
                        self._set_progress_ui(1, self.t("status.progress_done"))
                        self._set_status(self.t("status.project_generated_saved"), success=True)
                    else:
                        self._set_status(self.t("status.project_generated_rendering"))
                elif event_type == "video":
                    self._set_progress_ui(1, self.t("progress.completed_detail"))
                    result = event["result"]
                    destination = result.file_path or result.remote_video_url or result.remote_video_id
                    workers_used = result.metadata.get("workers_used")
                    encoder_used = result.metadata.get("video_encoder")
                    render_devices_used = str(result.metadata.get("video_render_devices") or self.video_render_device_var.get() or "Auto")
                    render_parts = [self.t("status.render_chip", provider=result.provider)]
                    if encoder_used:
                        render_parts.append(f"Encoder: {encoder_used}")
                    if workers_used:
                        render_parts.append(f"Workers: {workers_used}")
                    self.render_chip.configure(text=" | ".join(render_parts))
                    if hasattr(self, "active_encoder_summary_label") and encoder_used:
                        self.active_encoder_summary_label.configure(
                            text=self.t("local_ai.active_encoder", encoder=encoder_used, device=render_devices_used)
                        )
                    if encoder_used:
                        self._set_status(self.t("status.video_completed_with_encoder", provider=result.provider, encoder=encoder_used, destination=destination), success=True)
                    else:
                        self._set_status(self.t("status.video_completed", provider=result.provider, destination=destination), success=True)
                elif event_type == "done":
                    self.is_busy = False
                    self._toggle_busy_state(False)
                    self._reset_auto_close_timer()
                self.task_queue.task_done()
        except queue.Empty:
            pass
        finally:
            if not self._closing and self.winfo_exists():
                self._process_queue_job_id = self.after(150, self._process_task_queue)

    def _toggle_busy_state(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        for button in [
            self.inspect_button,
            self.prepare_button,
            self.install_model_button,
            self.connection_button,
            self.local_video_button,
            self.generate_button,
            self.quick_generate_button,
            self.export_json_button,
            self.export_txt_button,
            self.export_csv_button,
            self.video_button,
            self.folder_button,
        ]:
            button.configure(state=state)
        self._sync_video_provider_ui()

    def _set_status(self, message: str, *, success: bool = False, error: bool = False) -> None:
        self.status_label.configure(text=message)
        if error:
            self.status_label.configure(text_color=THEME["status_error"])
        elif success:
            self.status_label.configure(text_color=THEME["status_success"])
        else:
            self.status_label.configure(text_color=THEME["status_default"])

    def _set_progress_ui(self, value: float, detail: str) -> None:
        normalized = max(0.0, min(1.0, float(value)))
        self.progress_bar.set(normalized)
        if hasattr(self, "progress_percent_label"):
            self.progress_percent_label.configure(text=f"{round(normalized * 100):d}%")
        if hasattr(self, "progress_detail_label") and detail:
            self.progress_detail_label.configure(text=self.t("progress.detail", detail=detail))

    def _render_project(self, project: VideoProject) -> None:
        summary_lines = [
            f"{self.t('project.title')}: {project.title}",
            f"{self.t('project.summary')}: {project.summary}",
            f"{self.t('project.general_script')}: {project.general_script}",
            f"{self.t('project.structure')}: {project.structure}",
            f"{self.t('project.language')}: {project.output_language}",
            f"{self.t('project.mode')}: {project.generation_mode}",
            f"{self.t('project.topic')}: {project.source_topic}",
            f"{self.t('project.visual_style')}: {project.visual_style}",
            f"{self.t('project.audience')}: {project.audience}",
            f"{self.t('project.narrative_tone')}: {project.narrative_tone}",
            f"{self.t('project.format')}: {project.video_format}",
            f"{self.t('project.estimated_duration')}: {project.estimated_total_duration_seconds}s",
        ]
        scene_blocks = []
        for scene in project.scenes:
            scene_blocks.append(
                "\n".join(
                    [
                        f"{self.t('project.scene')} {scene.scene_number}: {scene.scene_title}",
                        f"{self.t('project.description')}: {scene.description}",
                        f"{self.t('project.visual_description')}: {scene.visual_description or self.t('project.not_requested')}",
                        f"{self.t('project.visual_prompt')}: {scene.visual_prompt or self.t('project.not_requested')}",
                        f"{self.t('project.cinematic_intent')}: {scene.cinematic_intent or self.t('project.auto')}",
                        f"{self.t('project.camera_language')}: {scene.camera_language or self.t('project.auto')}",
                        f"{self.t('project.lighting_style')}: {scene.lighting_style or self.t('project.auto')}",
                        f"{self.t('project.color_palette')}: {scene.color_palette or self.t('project.auto')}",
                        f"{self.t('project.energy_level')}: {scene.energy_level or self.t('project.auto')}",
                        f"{self.t('project.negative_prompt')}: {scene.negative_prompt or self.t('project.negative_prompt_default')}",
                        f"{self.t('project.shots')}: {summarize_scene_shots(scene) or self.t('project.shots_auto')}",
                        f"{self.t('project.narration')}: {scene.narration}",
                        f"{self.t('project.duration')}: {scene.duration_seconds}s",
                        f"{self.t('project.transition')}: {scene.transition}",
                    ]
                )
            )
        self._write_textbox(self.summary_text, "\n".join(summary_lines))
        self._write_textbox(self.scenes_text, "\n\n".join(scene_blocks))
        self._write_textbox(self.json_text, json.dumps(project.to_dict(), indent=2, ensure_ascii=False))
        self.tab_view.set(self._tab_names["summary"])

    def _write_textbox(self, textbox: ctk.CTkTextbox, content: str) -> None:
        textbox.configure(state="normal")
        textbox.delete("1.0", "end")
        textbox.insert("1.0", content)
        textbox.configure(state="disabled")

    def _load_models_background(self) -> None:
        try:
            client = self._build_client()
        except Exception as exc:
            self._set_status(str(exc), error=True)
            return

        def worker() -> None:
            success, models, message = client.test_connection()
            if not success:
                raise requests.RequestException(message)
            self._queue_event("connection", models=models, message=message)

        self._run_in_background(self.t("tasks.test_lmstudio"), worker)

    def test_connection(self) -> None:
        self._load_models_background()

    def test_local_video_connection(self) -> None:
        try:
            client = self._build_local_video_client()
        except Exception as exc:
            self._set_status(str(exc), error=True)
            return

        def worker() -> None:
            success, message = client.test_connection()
            if not success:
                raise requests.RequestException(message)
            checkpoints = client.list_checkpoints()
            details = message if not checkpoints else f"{message} {len(checkpoints)} visual model(s) detected."
            details = f"{details} URL: {self.comfyui_base_url_var.get().strip() or 'http://127.0.0.1:8188'}"
            self._queue_event("local_video_connection", message=details, checkpoints=checkpoints)

        self._run_in_background(self.t("tasks.test_comfyui"), worker)

    def inspect_environment(self) -> None:
        if self.is_busy:
            return

        def worker() -> None:
            status = self.setup_manager.inspect_environment(self._build_environment_config_snapshot())
            self._queue_event(
                "environment",
                status=status,
                checkpoints=status.checkpoints,
                updates={
                    "comfyui_base_url": status.comfyui_base_url,
                    "comfyui_worker_urls": ", ".join(status.comfyui_worker_urls),
                    "parallel_scene_workers": max(1, len(status.comfyui_worker_urls)),
                },
                gpu_names=status.gpu_names,
                message=self.t("status.environment_analysis_updated"),
                success=status.ffmpeg_ready or status.comfyui_reachable or status.lmstudio_installed,
            )

        self._run_in_background(self.t("tasks.inspect_environment"), worker)

    def prepare_environment(self) -> None:
        def worker() -> None:
            result = self.setup_manager.prepare_environment(
                self._build_environment_config_snapshot(),
                install_missing=True,
                install_default_checkpoint=True,
                progress_callback=lambda value, message: self._queue_event("progress", value=value, message=message),
            )
            self.config_manager.update(**result.updates)
            self.app_config = self.config_manager.config
            self._queue_event(
                "environment",
                status=result.status,
                checkpoints=result.status.checkpoints,
                updates=result.updates,
                gpu_names=result.status.gpu_names,
                message=self.t("status.setup_completed"),
                success=result.status.ffmpeg_ready or result.status.workflow_ready or bool(result.status.comfyui_checkpoint),
            )

        self._run_in_background(self.t("tasks.prepare_environment"), worker)

    def install_recommended_checkpoint(self) -> None:
        def worker() -> None:
            self.setup_manager.ensure_extra_models_config()
            downloaded = self.setup_manager.download_default_checkpoint(
                progress_callback=lambda value, message: self._queue_event(
                    "progress",
                    value=0.1 + (value * 0.8),
                    message=message,
                )
            )
            updates = {
                "comfyui_checkpoint": downloaded.name,
                "comfyui_workflow_path": str(
                    self.setup_manager.ensure_default_workflow(
                        checkpoint_name=downloaded.name,
                        aspect_ratio=self.video_aspect_ratio_var.get().strip() or "9:16",
                    )
                ),
                "video_provider": "Local AI video",
                "comfyui_base_url": self.setup_manager.resolve_comfyui_base_url(self.comfyui_base_url_var.get().strip()),
            }
            resolved_workers = self.setup_manager.resolve_comfyui_worker_urls(
                self.comfyui_worker_urls_var.get().strip(),
                str(updates["comfyui_base_url"]),
            )
            updates["comfyui_worker_urls"] = ", ".join(resolved_workers)
            updates["parallel_scene_workers"] = max(1, len(resolved_workers))
            self.config_manager.update(**updates)
            self.app_config = self.config_manager.config
            status = self.setup_manager.inspect_environment(self._build_environment_config_snapshot())
            self._queue_event(
                "environment",
                status=status,
                checkpoints=status.checkpoints,
                updates=updates,
                gpu_names=status.gpu_names,
                message=self.t("status.recommended_checkpoint_installed"),
                success=True,
            )

        self._run_in_background(self.t("tasks.install_checkpoint"), worker)

    def start_generation(self) -> None:
        try:
            client = self._build_client()
            request = self._build_request()
            retry_attempts = self._safe_positive_int(self.retries_var.get(), 3)
        except Exception as exc:
            self._set_status(str(exc), error=True)
            return

        def worker() -> None:
            self._queue_event("progress", value=0.05, message=self.t("progress.preparing_generation_request"))
            if not request.model:
                models = client.list_models()
                if not models:
                    raise ValueError(self.t("errors.no_models_available"))
                request.model = models[0]
                self._queue_event("connection", models=models, message=self.t("status.connected_using_model", model=models[0]))

            project = self.generator_service.generate(
                client=client,
                request=request,
                retry_attempts=retry_attempts,
                progress_callback=lambda value, message: self._queue_event("progress", value=value, message=message),
            )
            history_path = self.history_service.save(project)
            self._queue_event("project", project=project, history_path=history_path, finished=True)

        self._run_in_background(self.t("tasks.generate_project"), worker)

    def generate_full_video(self) -> None:
        try:
            request = self._build_request()
            if request.generation_mode != "Proyecto completo":
                request.generation_mode = "Proyecto completo"
                self.mode_var.set("Proyecto completo")
                self._schedule_save()
            self._apply_silent_narration_override(provider=self.video_provider_var.get().strip() or "Storyboard local")
            retry_attempts = self._safe_positive_int(self.retries_var.get(), 3)
            render_settings = self._capture_video_render_settings()
        except Exception as exc:
            self._set_status(str(exc), error=True)
            return

        def worker() -> None:
            self._queue_event("progress", value=0.03, message=self.t("progress.preparing_full_video"))
            try:
                render_settings_local = self._prepare_render_settings_for_full_video(dict(render_settings))
            except LocalAIVideoWorkflowError:
                raise
            except Exception as exc:
                self.logger.warning("Automatic environment preparation failed, falling back to Storyboard local: %s", exc)
                render_settings_local = dict(render_settings)
                render_settings_local["provider"] = "Storyboard local"
                ffmpeg_path = self.setup_manager.ensure_ffmpeg_ready(render_settings_local["ffmpeg_path"], install_missing=True)
                if ffmpeg_path:
                    self._persist_runtime_updates(
                        render_settings_local,
                        {"ffmpeg_path": ffmpeg_path, "video_provider": "Storyboard local"},
                        message=self.t("progress.auto_setup_storyboard"),
                    )
                self._queue_event("progress", value=0.2, message=self.t("progress.auto_setup_storyboard"))

            client, lmstudio_ready, models = self._ensure_lmstudio_ready_for_generation(request)

            if lmstudio_ready and (request.model or models):
                if not request.model and models:
                    request.model = models[0]
                try:
                    project = self.generator_service.generate(
                        client=client,
                        request=request,
                        retry_attempts=retry_attempts,
                        progress_callback=lambda value, message: self._queue_event(
                            "progress",
                            value=min(0.55, value * 0.55),
                            message=message,
                        ),
                    )
                except Exception as exc:
                    self.logger.warning("LM Studio generation failed, using local fallback project: %s", exc)
                    self._queue_event(
                        "progress",
                        value=0.24,
                        message=self.t("progress.lmstudio_timeout_local"),
                    )
                    project = self.generator_service.generate_fallback_project(request)
            else:
                self._queue_event(
                    "progress",
                    value=0.24,
                    message=self.t("progress.lmstudio_unavailable_local"),
                )
                project = self.generator_service.generate_fallback_project(request)

            history_path = self.history_service.save(project)
            self._queue_event("project", project=project, history_path=history_path, finished=False)

            render_request = self._build_video_render_request_for_project(project, render_settings_local)
            try:
                result = self.video_render_service.render(
                    render_request,
                    progress_callback=lambda value, message: self._queue_event(
                        "progress",
                        value=0.55 + (value * 0.45),
                        message=message,
                    ),
                )
            except LocalAIVideoWorkflowError:
                raise
            except Exception as exc:
                if render_request.provider != "Local AI video":
                    raise
                self.logger.warning("Local AI render failed, falling back to Storyboard local: %s", exc)
                self._queue_event(
                    "progress",
                    value=0.62,
                    message=self.t("progress.local_ai_failed_storyboard"),
                )
                render_settings_local["provider"] = "Storyboard local"
                ffmpeg_path = self.setup_manager.ensure_ffmpeg_ready(render_settings_local["ffmpeg_path"], install_missing=True)
                if ffmpeg_path:
                    render_settings_local["ffmpeg_path"] = ffmpeg_path
                    self._persist_runtime_updates(
                        render_settings_local,
                        {"ffmpeg_path": ffmpeg_path, "video_provider": "Storyboard local"},
                        message=self.t("progress.local_ai_failed_storyboard"),
                    )
                fallback_request = self._build_video_render_request_for_project(project, render_settings_local)
                result = self.video_render_service.render(
                    fallback_request,
                    progress_callback=lambda value, message: self._queue_event(
                        "progress",
                        value=0.55 + (value * 0.45),
                        message=message,
                    ),
                )
            self._queue_event("video", result=result)

        self._run_in_background(self.t("tasks.generate_full_video"), worker)

    def export_json(self) -> None:
        if not self.current_project:
            self._set_status(self.t("status.nothing_to_export"), error=True)
            return
        file_path = self.export_service.export_json(self.current_project, self.config_manager.resolve_output_dir())
        self._set_status(self.t("status.json_exported", path=file_path), success=True)

    def export_txt(self) -> None:
        if not self.current_project:
            self._set_status(self.t("status.nothing_to_export"), error=True)
            return
        file_path = self.export_service.export_txt(self.current_project, self.config_manager.resolve_output_dir())
        self._set_status(self.t("status.txt_exported", path=file_path), success=True)

    def export_csv(self) -> None:
        if not self.current_project:
            self._set_status(self.t("status.nothing_to_export"), error=True)
            return
        file_path = self.export_service.export_csv(self.current_project, self.config_manager.resolve_output_dir())
        self._set_status(self.t("status.csv_exported", path=file_path), success=True)

    def generate_video(self) -> None:
        try:
            self._apply_silent_narration_override(self.current_project)
            render_request = self._build_video_render_request()
        except Exception as exc:
            self._set_status(str(exc), error=True)
            return

        def worker() -> None:
            result = self.video_render_service.render(
                render_request,
                progress_callback=lambda value, message: self._queue_event("progress", value=value, message=message),
            )
            self._queue_event("video", result=result)

        self._run_in_background(self.t("tasks.generate_final_video", provider=render_request.provider), worker)

    def open_output_folder(self) -> None:
        output_path = Path(self.config_manager.resolve_output_dir())
        output_path.mkdir(parents=True, exist_ok=True)
        os.startfile(output_path)  # type: ignore[attr-defined]
        self._set_status(self.t("status.folder_opened", path=output_path))

    def launch_lmstudio(self) -> None:
        if self.setup_manager.launch_application("lmstudio"):
            self._set_status(self.t("status.lmstudio_opened"))
            return
        self._set_status(self.t("status.lmstudio_not_found"), error=True)

    def launch_comfyui(self) -> None:
        gpu_choice = self.render_gpu_var.get().strip() or "Auto"
        if self.setup_manager.launch_application(
            "comfyui",
            gpu_choice=gpu_choice,
            configured_url=self.comfyui_base_url_var.get().strip(),
        ):
            if gpu_choice == "Auto":
                self._set_status(self.t("status.comfyui_opened"))
            else:
                self._set_status(self.t("status.comfyui_opened_with_gpu", gpu=gpu_choice))
            return
        self._set_status(self.t("status.comfyui_not_found"), error=True)

    def open_comfyui_models_folder(self) -> None:
        folder = self.setup_manager.open_models_folder()
        self._set_status(self.t("status.models_folder_opened", path=folder))

    def choose_output_folder(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.config_manager.resolve_output_dir())
        if selected:
            self.output_dir_var.set(selected)
            self._save_gui_state()
            self._set_status(self.t("status.output_folder_updated", path=selected), success=True)

    def choose_avatar_image(self) -> None:
        selected = filedialog.askopenfilename(
            title=self.t("dialogs.avatar_image_title"),
            filetypes=[("Image files", "*.png *.jpg *.jpeg *.webp"), ("All files", "*.*")],
            initialdir=APP_ROOT,
        )
        if selected:
            self.avatar_source_image_path_var.set(selected)
            self._save_gui_state()
            self._set_status(self.t("status.avatar_image_updated", path=selected), success=True)

    def choose_comfyui_workflow(self) -> None:
        selected = filedialog.askopenfilename(
            title=self.t("dialogs.workflow_title"),
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            initialdir=APP_ROOT,
        )
        if selected:
            self.comfyui_workflow_path_var.set(selected)
            self._save_gui_state()
            workflow_mode = self._describe_workflow_mode(selected)
            if workflow_mode == "video":
                self._set_status(self.t("status.workflow_updated", path=selected), success=True)
            elif workflow_mode == "image":
                self._set_status(self.t("status.workflow_image_detected", path=selected), success=True)
            else:
                self._set_status(self.t("status.workflow_unknown_detected", path=selected), success=True)

    def choose_piper_model(self) -> None:
        selected = filedialog.askopenfilename(
            title=self.t("dialogs.piper_model_title"),
            filetypes=[("ONNX files", "*.onnx"), ("All files", "*.*")],
            initialdir=APP_ROOT,
        )
        if selected:
            self.piper_model_path_var.set(selected)
            self._save_gui_state()
            self._set_status(self.t("status.model_updated", path=selected), success=True)

    def _load_history_buttons(self) -> None:
        for child in self.history_scroll.winfo_children():
            child.destroy()

        entries = self.history_service.list_entries(limit=self.app_config.history_limit)
        if not entries:
            ctk.CTkLabel(
                self.history_scroll,
                text=self.t("history.empty"),
                text_color=THEME["muted_text"],
            ).grid(row=0, column=0, sticky="w", padx=12, pady=12)
            return

        for index, entry in enumerate(entries):
            button = ctk.CTkButton(
                self.history_scroll,
                text=entry.file_path.stem,
                anchor="w",
                fg_color=THEME["history_button"],
                text_color=THEME["primary_text"],
                hover_color=THEME["history_hover"],
                command=lambda item=entry: self.load_history_entry(item),
            )
            button.grid(row=index, column=0, sticky="ew", padx=10, pady=(8 if index == 0 else 0, 8))

    def load_history_entry(self, entry: HistoryEntry) -> None:
        try:
            project = self.history_service.load(entry.file_path)
        except Exception as exc:
            self._set_status(self.t("status.history_load_failed", error=exc), error=True)
            return
        self.current_project = project
        self.current_history_path = entry.file_path
        self.topic_text.delete("1.0", "end")
        self.topic_text.insert("1.0", project.source_topic)
        self.visual_style_var.set(project.visual_style)
        self.audience_var.set(project.audience)
        self.tone_var.set(project.narrative_tone)
        self.format_var.set(project.video_format)
        self.language_var.set(project.output_language)
        self.mode_var.set(project.generation_mode)
        self.duration_var.set(str(project.estimated_total_duration_seconds))
        self.scene_count_var.set(str(len(project.scenes)))
        self._schedule_save()
        self._render_project(project)
        self._set_status(self.t("status.history_loaded", name=entry.file_path.name), success=True)

    def show_about_dialog(self) -> None:
        dialog = ctk.CTkToplevel(self)
        dialog.title(self.t("about.title"))
        dialog.geometry("500x190")
        dialog.resizable(False, False)
        dialog.configure(fg_color=THEME["status_bar"])
        dialog.transient(self)
        dialog.grab_set()

        year = datetime.now().year
        text = self.t("about.copyright", version=DISPLAY_VERSION, year=year)
        ctk.CTkLabel(
            dialog,
            text=APP_NAME,
            text_color=THEME["hero_text"],
            font=ctk.CTkFont("Segoe UI Variable Display", 28, weight="bold"),
        ).pack(pady=(26, 8))
        ctk.CTkLabel(
            dialog,
            text=text,
            text_color=THEME["soft_text"],
            font=ctk.CTkFont("Segoe UI", 15),
            wraplength=420,
            justify="center",
        ).pack(padx=18, pady=(0, 16))
        ctk.CTkButton(
            dialog,
            text=self.t("about.close"),
            command=dialog.destroy,
            fg_color=ui_color("#EA580C", "#EA580C"),
            hover_color=ui_color("#C2410C", "#C2410C"),
            width=120,
        ).pack()

    def reset_form(self) -> None:
        self.topic_text.delete("1.0", "end")
        self.visual_style_var.set("Cyberpunk cinematografico")
        self.audience_var.set("General")
        self.tone_var.set("Cinematic and immersive")
        self.format_var.set("YouTube Short")
        self.mode_var.set("Proyecto completo")
        self.language_var.set("Espanol")
        self.scene_count_var.set("6")
        self.duration_var.set("60")
        self._schedule_save()
        self._set_status(self.t("status.project_reset"))

    def _reset_auto_close_timer(self) -> None:
        self._auto_close_remaining = max(1, self._safe_positive_int(self.auto_close_seconds_var.get(), 60))
        self._update_countdown_label()

    def _update_countdown_label(self) -> None:
        if not self.auto_close_var.get():
            self.countdown_label.configure(text=self.t("countdown.off", version=DISPLAY_VERSION))
            return
        if self.is_busy:
            self.countdown_label.configure(text=self.t("countdown.paused", version=DISPLAY_VERSION))
            return
        self.countdown_label.configure(text=self.t("countdown.in", version=DISPLAY_VERSION, seconds=self._auto_close_remaining))

    def _tick_auto_close(self) -> None:
        if self._closing or not self.winfo_exists():
            return
        if self.auto_close_var.get() and not self.is_busy:
            self._auto_close_remaining -= 1
            if self._auto_close_remaining <= 0:
                self._set_status(self.t("status.auto_close_zero"))
                self._auto_close_trigger_job_id = self.after(150, self._on_close)
                return
        self._update_countdown_label()
        self._countdown_job_id = self.after(1000, self._tick_auto_close)

    def _on_close(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._cancel_scheduled_jobs()
        self._save_gui_state()
        self._save_window_geometry()
        if self.winfo_exists():
            try:
                self.quit()
            except tk.TclError:
                pass
            self.after_idle(self.destroy)


def run() -> None:
    app = VideoGeniusApp()
    app.mainloop()
