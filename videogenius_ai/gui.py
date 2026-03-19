from __future__ import annotations

from dataclasses import replace
import json
import os
import queue
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog
from typing import Any, Callable

import customtkinter as ctk
import requests

from .comfyui_client import ComfyUIClient
from .config import AppConfig, ConfigManager, sanitize_window_geometry
from .export_service import ExportService
from .generator_service import SceneGeneratorService
from .history_service import HistoryEntry, HistoryService
from .lmstudio_client import LMStudioClient
from .logging_utils import configure_logging
from .models import GenerationRequest, VideoProject, VideoRenderRequest
from .paths import APP_ROOT
from .setup_manager import COMFYUI_PACKAGE_ID, LM_STUDIO_PACKAGE_ID, SetupManager
from .version import APP_NAME, DISPLAY_VERSION
from .video_render_service import VideoRenderService

def ui_color(light: str, dark: str) -> tuple[str, str]:
    return (light, dark)


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

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


class VideoGeniusApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self._alpha_hidden = False
        try:
            self.attributes("-alpha", 0.0)
            self._alpha_hidden = True
        except tk.TclError:
            self._alpha_hidden = False
        self.logger = configure_logging()
        self.config_manager = ConfigManager()
        self.app_config: AppConfig = self.config_manager.config
        ctk.set_appearance_mode(self._normalize_appearance_mode(self.app_config.appearance_mode))
        self.setup_manager = SetupManager()
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
        self._auto_close_remaining = max(1, int(self.app_config.auto_close_seconds))

        self._configure_root()
        self._create_variables()
        self._build_menu()
        self._build_layout()
        self._sync_video_provider_ui()
        self._sync_tts_ui()
        self._bind_shortcuts()
        self._bind_activity_reset()
        self._load_history_buttons()
        self._set_status("Ready. Configure LM Studio and generate a project.")
        self._schedule_initial_window_show()
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
        if self.app_config.window_zoomed:
            self.state("zoomed")
        else:
            self.geometry(self._geometry_for_current_screen(self.app_config.window_geometry))
        self.title(f"{APP_NAME} {DISPLAY_VERSION}")
        if self._alpha_hidden:
            try:
                self.attributes("-alpha", 1.0)
            except tk.TclError:
                pass

    def _normalize_appearance_mode(self, value: str) -> str:
        normalized = (value or "dark").strip().lower()
        if normalized not in {"light", "dark", "system"}:
            return "dark"
        return normalized

    def _appearance_label_to_mode(self, value: str) -> str:
        mapping = {"Claro": "light", "Oscuro": "dark", "Sistema": "system"}
        return mapping.get(value, self._normalize_appearance_mode(value))

    def _appearance_mode_to_label(self, value: str) -> str:
        mapping = {"light": "Claro", "dark": "Oscuro", "system": "Sistema"}
        return mapping.get(self._normalize_appearance_mode(value), "Oscuro")

    def _apply_user_appearance_mode(self, mode: str) -> None:
        ctk.set_appearance_mode(self._normalize_appearance_mode(mode))

    def _create_variables(self) -> None:
        self.appearance_mode_var = tk.StringVar(value=self._appearance_mode_to_label(self.app_config.appearance_mode))
        self.base_url_var = tk.StringVar(value=self.app_config.lmstudio_base_url)
        self.model_var = tk.StringVar(value=self.app_config.model)
        self.api_key_var = tk.StringVar(value=self.app_config.api_key)
        self.video_provider_var = tk.StringVar(value=self.app_config.video_provider)
        self.video_aspect_ratio_var = tk.StringVar(value=self.app_config.video_aspect_ratio)
        self.render_captions_var = tk.BooleanVar(value=self.app_config.render_captions)
        self.comfyui_base_url_var = tk.StringVar(value=self.app_config.comfyui_base_url)
        self.comfyui_worker_urls_var = tk.StringVar(value=self.app_config.comfyui_worker_urls)
        self.parallel_scene_workers_var = tk.StringVar(value=str(self.app_config.parallel_scene_workers))
        self.comfyui_checkpoint_var = tk.StringVar(value=self.app_config.comfyui_checkpoint)
        self.comfyui_workflow_path_var = tk.StringVar(value=self.app_config.comfyui_workflow_path)
        self.comfyui_negative_prompt_var = tk.StringVar(value=self.app_config.comfyui_negative_prompt)
        self.comfyui_poll_interval_var = tk.StringVar(value=str(self.app_config.comfyui_poll_interval_seconds))
        self.tts_backend_var = tk.StringVar(value=self.app_config.tts_backend)
        self.ffmpeg_path_var = tk.StringVar(value=self.app_config.ffmpeg_path)
        self.piper_executable_path_var = tk.StringVar(value=self.app_config.piper_executable_path)
        self.piper_model_path_var = tk.StringVar(value=self.app_config.piper_model_path)
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
            self.comfyui_checkpoint_var,
            self.comfyui_workflow_path_var,
            self.comfyui_negative_prompt_var,
            self.comfyui_poll_interval_var,
            self.tts_backend_var,
            self.ffmpeg_path_var,
            self.piper_executable_path_var,
            self.piper_model_path_var,
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

    def _build_menu(self) -> None:
        menu_bar = tk.Menu(self)

        file_menu = tk.Menu(menu_bar, tearoff=0)
        file_menu.add_command(label="Nuevo", accelerator="Ctrl+N", command=self.reset_form)
        file_menu.add_command(label="Analizar entorno", accelerator="Ctrl+I", command=self.inspect_environment)
        file_menu.add_command(label="Preparar entorno automatico", accelerator="Ctrl+Shift+I", command=self.prepare_environment)
        file_menu.add_command(label="Probar conexion", accelerator="Ctrl+L", command=self.test_connection)
        file_menu.add_command(label="Probar ComfyUI", accelerator="Ctrl+H", command=self.test_local_video_connection)
        file_menu.add_command(label="Generar guion", accelerator="Ctrl+G", command=self.start_generation)
        file_menu.add_command(label="Generar video completo", accelerator="Ctrl+Shift+G", command=self.generate_full_video)
        file_menu.add_separator()
        file_menu.add_command(label="Exportar JSON", accelerator="Ctrl+J", command=self.export_json)
        file_menu.add_command(label="Exportar TXT", accelerator="Ctrl+T", command=self.export_txt)
        file_menu.add_command(label="Exportar CSV", accelerator="Ctrl+E", command=self.export_csv)
        file_menu.add_command(label="Generar video", accelerator="Ctrl+M", command=self.generate_video)
        file_menu.add_separator()
        file_menu.add_command(label="Abrir carpeta de salida", accelerator="Ctrl+O", command=self.open_output_folder)
        file_menu.add_command(label="Salir", accelerator="Ctrl+Q", command=self._on_close)
        menu_bar.add_cascade(label="Archivo", menu=file_menu)

        help_menu = tk.Menu(menu_bar, tearoff=0)
        help_menu.add_command(label="About", accelerator="F1", command=self.show_about_dialog)
        menu_bar.add_cascade(label="Vista", menu=self._build_view_menu(menu_bar))
        menu_bar.add_cascade(label="Ayuda", menu=help_menu)
        self.config(menu=menu_bar)

    def _build_view_menu(self, menu_bar: tk.Menu) -> tk.Menu:
        view_menu = tk.Menu(menu_bar, tearoff=0)
        view_menu.add_radiobutton(label="Tema oscuro", value="Oscuro", variable=self.appearance_mode_var, command=self._on_appearance_change)
        view_menu.add_radiobutton(label="Tema claro", value="Claro", variable=self.appearance_mode_var, command=self._on_appearance_change)
        view_menu.add_radiobutton(label="Tema del sistema", value="Sistema", variable=self.appearance_mode_var, command=self._on_appearance_change)
        view_menu.add_separator()
        view_menu.add_command(label="Alternar oscuro/claro", accelerator="Ctrl+Shift+D", command=self.toggle_dark_mode)
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
            text=f"{DISPLAY_VERSION} | Auto-close off",
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
            text=f"{DISPLAY_VERSION}  |  LM Studio + local AI video desktop",
            text_color=THEME["accent"],
            font=ctk.CTkFont("Segoe UI", 13),
        ).grid(row=1, column=0, sticky="w", padx=18, pady=(0, 4))
        ctk.CTkLabel(
            hero,
            text="Turn an idea into script, prompts, local AI visuals, storyboard fallback, and MP4 output without blocking the UI.",
            text_color=THEME["soft_text"],
            justify="left",
            wraplength=340,
            font=ctk.CTkFont("Segoe UI", 14),
        ).grid(row=2, column=0, sticky="w", padx=18, pady=(0, 16))

        row = 1
        topic_card = self._make_card(self.sidebar, "Project brief", "Describe the idea the model should develop.")
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

        setup_card = self._make_card(self.sidebar, "Instalacion guiada", "La app puede detectar, instalar y preconfigurar el entorno local para un usuario no tecnico.")
        setup_card.grid(row=row, column=0, sticky="ew", padx=10, pady=8)
        self.setup_summary_label = ctk.CTkLabel(
            setup_card,
            text="Analizando entorno...",
            text_color=ui_color("#C7D2FE", "#C7D2FE"),
            justify="left",
            wraplength=330,
            font=ctk.CTkFont("Segoe UI", 12),
        )
        self.setup_summary_label.grid(row=2, column=0, sticky="w", padx=14, pady=(4, 10))
        self.inspect_button = ctk.CTkButton(
            setup_card,
            text="Analizar entorno (Ctrl+I)",
            command=self.inspect_environment,
            fg_color=ui_color("#0369A1", "#0369A1"),
            hover_color=ui_color("#075985", "#075985"),
        )
        self.inspect_button.grid(row=3, column=0, sticky="ew", padx=14, pady=(0, 8))
        self.prepare_button = ctk.CTkButton(
            setup_card,
            text="Preparar entorno automatico",
            command=self.prepare_environment,
            fg_color=ui_color("#15803D", "#15803D"),
            hover_color=ui_color("#166534", "#166534"),
        )
        self.prepare_button.grid(row=4, column=0, sticky="ew", padx=14, pady=(0, 8))
        self.launch_lmstudio_button = ctk.CTkButton(
            setup_card,
            text="Abrir LM Studio",
            command=self.launch_lmstudio,
            fg_color=ui_color("#4F46E5", "#4F46E5"),
            hover_color=ui_color("#4338CA", "#4338CA"),
        )
        self.launch_lmstudio_button.grid(row=5, column=0, sticky="ew", padx=14, pady=(0, 8))
        self.launch_comfyui_button = ctk.CTkButton(
            setup_card,
            text="Abrir ComfyUI",
            command=self.launch_comfyui,
            fg_color=ui_color("#7C3AED", "#7C3AED"),
            hover_color=ui_color("#6D28D9", "#6D28D9"),
        )
        self.launch_comfyui_button.grid(row=6, column=0, sticky="ew", padx=14, pady=(0, 10))
        self.install_model_button = ctk.CTkButton(
            setup_card,
            text="Instalar modelo base recomendado",
            command=self.install_recommended_checkpoint,
            fg_color=ui_color("#B45309", "#B45309"),
            hover_color=ui_color("#92400E", "#92400E"),
        )
        self.install_model_button.grid(row=7, column=0, sticky="ew", padx=14, pady=(0, 8))
        self.open_models_button = ctk.CTkButton(
            setup_card,
            text="Abrir carpeta de modelos",
            command=self.open_comfyui_models_folder,
            fg_color=ui_color("#1E293B", "#334155"),
            hover_color=ui_color("#0F172A", "#1E293B"),
        )
        self.open_models_button.grid(row=8, column=0, sticky="ew", padx=14, pady=(0, 10))
        row += 1

        profile_card = self._make_card(self.sidebar, "Quick setup", "Write the prompt, choose the style, and generate the full video from here.")
        profile_card.grid(row=row, column=0, sticky="ew", padx=10, pady=8)
        self._make_labeled_entry(profile_card, 2, "Visual style", self.visual_style_var)
        self._make_labeled_combo(profile_card, 4, "Audience", self.audience_var, ["General", "Gamers", "Students", "Professionals", "Children"])
        self._make_labeled_combo(profile_card, 6, "Narrative tone", self.tone_var, ["Cinematic and immersive", "Educational", "Epic", "Emotional", "Fast-paced"])
        self._make_labeled_combo(profile_card, 8, "Video format", self.format_var, ["YouTube Short", "TikTok", "Instagram Reel", "YouTube Long", "Trailer"])
        self._make_labeled_entry(profile_card, 10, "Estimated duration (s)", self.duration_var)
        self.video_provider_combo = self._make_labeled_combo(
            profile_card,
            12,
            "Render backend",
            self.video_provider_var,
            ["Storyboard local", "Local AI video"],
        )
        self.video_provider_combo.configure(command=lambda _value: self._on_video_provider_change())
        self._make_labeled_combo(
            profile_card,
            14,
            "Aspect ratio",
            self.video_aspect_ratio_var,
            ["9:16", "16:9", "1:1"],
        )
        self.render_captions_checkbox = ctk.CTkCheckBox(
            profile_card,
            text="Burn local captions on the final video",
            variable=self.render_captions_var,
            checkbox_width=20,
            checkbox_height=20,
            text_color=THEME["hero_text"],
        )
        self.render_captions_checkbox.grid(row=16, column=0, sticky="w", padx=14, pady=(4, 8))
        self.quick_generate_button = ctk.CTkButton(
            profile_card,
            text="Generar video completo (Ctrl+Shift+G)",
            command=self.generate_full_video,
            height=44,
            fg_color=ui_color("#EA580C", "#EA580C"),
            hover_color=ui_color("#C2410C", "#C2410C"),
        )
        self.quick_generate_button.grid(row=17, column=0, sticky="ew", padx=14, pady=(2, 12))
        ctk.CTkLabel(
            profile_card,
            text="Usa los valores por defecto si no quieres tocar los ajustes tecnicos.",
            text_color=ui_color("#94A3B8", "#94A3B8"),
            wraplength=330,
            justify="left",
            font=ctk.CTkFont("Segoe UI", 12),
        ).grid(row=18, column=0, sticky="w", padx=14, pady=(0, 10))
        row += 1

        lm_card = self._make_card(self.sidebar, "LM Studio", "OpenAI-compatible local backend settings.")
        lm_card.grid(row=row, column=0, sticky="ew", padx=10, pady=8)
        self._make_labeled_entry(lm_card, 2, "Base URL", self.base_url_var)
        self.model_combo = self._make_labeled_combo(lm_card, 4, "Model", self.model_var, [""])

        ctk.CTkLabel(
            lm_card,
            text="LM Studio API key (optional)",
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
            text="Mostrar",
            width=90,
            command=self.toggle_api_key_visibility,
            fg_color=ui_color("#1D4ED8", "#2563EB"),
            hover_color=ui_color("#1E40AF", "#1D4ED8"),
        )
        self.show_api_key_button.grid(row=0, column=1, padx=(10, 0))

        slider_row = ctk.CTkFrame(lm_card, fg_color="transparent")
        slider_row.grid(row=8, column=0, padx=14, pady=(6, 2), sticky="ew")
        slider_row.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(slider_row, text="Temperature", text_color=THEME["card_label"], font=ctk.CTkFont("Segoe UI", 13, weight="bold")).grid(row=0, column=0, sticky="w")
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

        self._make_labeled_entry(lm_card, 10, "Scene count", self.scene_count_var)
        self._make_labeled_combo(lm_card, 12, "Output language", self.language_var, ["Espanol", "English", "Portugues", "Frances"])
        row += 1

        self.local_ai_card = self._make_card(self.sidebar, "Local AI backend", "Needed only when the backend is Local AI video.")
        self.local_ai_card.grid(row=row, column=0, sticky="ew", padx=10, pady=8)
        self._make_labeled_entry(self.local_ai_card, 2, "ComfyUI base URL", self.comfyui_base_url_var)
        self._make_labeled_entry(self.local_ai_card, 4, "ComfyUI worker URLs", self.comfyui_worker_urls_var)
        self._make_labeled_entry(self.local_ai_card, 6, "Parallel workers", self.parallel_scene_workers_var)
        self.comfyui_checkpoint_combo = self._make_labeled_combo(self.local_ai_card, 8, "Visual model", self.comfyui_checkpoint_var, [""])
        self._make_labeled_entry(self.local_ai_card, 10, "Workflow JSON path", self.comfyui_workflow_path_var)
        workflow_button = ctk.CTkButton(
            self.local_ai_card,
            text="Browse or keep auto workflow",
            command=self.choose_comfyui_workflow,
            fg_color=ui_color("#0F766E", "#0F766E"),
            hover_color=ui_color("#115E59", "#134E4A"),
        )
        workflow_button.grid(row=12, column=0, sticky="ew", padx=14, pady=(0, 10))
        self._make_labeled_entry(self.local_ai_card, 13, "Negative prompt", self.comfyui_negative_prompt_var)
        self._make_labeled_entry(self.local_ai_card, 15, "FFmpeg path", self.ffmpeg_path_var)
        self._make_labeled_entry(self.local_ai_card, 17, "ComfyUI poll interval (s)", self.comfyui_poll_interval_var)
        self._make_labeled_combo(self.local_ai_card, 19, "TTS backend", self.tts_backend_var, ["Windows local", "Sin voz", "Piper local"])
        self.piper_executable_entry = self._make_labeled_entry(self.local_ai_card, 21, "Piper executable", self.piper_executable_path_var)
        self.piper_model_entry = self._make_labeled_entry(self.local_ai_card, 23, "Piper model", self.piper_model_path_var)
        self.piper_button = ctk.CTkButton(
            self.local_ai_card,
            text="Browse Piper model",
            command=self.choose_piper_model,
            fg_color=ui_color("#2563EB", "#1D4ED8"),
            hover_color=ui_color("#1E40AF", "#1E3A8A"),
        )
        self.piper_button.grid(row=25, column=0, sticky="ew", padx=14, pady=(0, 6))
        ctk.CTkLabel(
            self.local_ai_card,
            text="Consejo: usa 'Windows local' para voz sin instalar Piper. Si ejecutas varias instancias de ComfyUI en puertos distintos, agrega todas las URLs y la app repartira escenas entre los workers.",
            text_color=ui_color("#94A3B8", "#94A3B8"),
            wraplength=330,
            justify="left",
            font=ctk.CTkFont("Segoe UI", 12),
        ).grid(row=26, column=0, sticky="w", padx=14, pady=(0, 10))
        row += 1

        advanced_card = self._make_card(self.sidebar, "Automation and advanced", "Saved in config.json next to the app.")
        advanced_card.grid(row=row, column=0, sticky="ew", padx=10, pady=8)
        self.appearance_combo = self._make_labeled_combo(advanced_card, 2, "Tema", self.appearance_mode_var, ["Oscuro", "Claro", "Sistema"])
        self.appearance_combo.configure(command=lambda _value: self._on_appearance_change())
        self._make_labeled_entry(advanced_card, 4, "Output folder", self.output_dir_var)
        browse_button = ctk.CTkButton(
            advanced_card,
            text="Browse",
            command=self.choose_output_folder,
            fg_color=ui_color("#0F766E", "#0F766E"),
            hover_color=ui_color("#115E59", "#134E4A"),
        )
        browse_button.grid(row=6, column=0, sticky="ew", padx=14, pady=(0, 10))

        self.auto_start_checkbox = ctk.CTkCheckBox(
            advanced_card,
            text="Auto-start generation on launch",
            variable=self.auto_start_var,
            checkbox_width=20,
            checkbox_height=20,
            text_color=THEME["hero_text"],
        )
        self.auto_start_checkbox.grid(row=7, column=0, sticky="w", padx=14, pady=(2, 6))

        self.auto_close_checkbox = ctk.CTkCheckBox(
            advanced_card,
            text="Auto-close after inactivity",
            variable=self.auto_close_var,
            checkbox_width=20,
            checkbox_height=20,
            text_color=THEME["hero_text"],
        )
        self.auto_close_checkbox.grid(row=8, column=0, sticky="w", padx=14, pady=(2, 6))

        self._make_labeled_entry(advanced_card, 9, "Auto-close seconds", self.auto_close_seconds_var)
        self._make_labeled_entry(advanced_card, 11, "JSON retry attempts", self.retries_var)
        self._make_labeled_entry(advanced_card, 13, "Request timeout (s)", self.timeout_var)
        self._make_labeled_entry(advanced_card, 15, "Max tokens", self.max_tokens_var)
        row += 1

        actions_card = self._make_card(self.sidebar, "Actions", "Keyboard shortcuts are also available from the menu.")
        actions_card.grid(row=row, column=0, sticky="ew", padx=10, pady=(8, 18))
        self.connection_button = self._make_action_button(actions_card, 2, "Probar conexion (Ctrl+L)", "#0284C7", "#0369A1", self.test_connection)
        self.local_video_button = self._make_action_button(actions_card, 3, "Probar ComfyUI (Ctrl+H)", "#0F766E", "#115E59", self.test_local_video_connection)
        self.generate_button = self._make_action_button(actions_card, 4, "Generar guion (Ctrl+G)", "#EA580C", "#C2410C", self.start_generation)
        self.export_json_button = self._make_action_button(actions_card, 5, "Exportar JSON (Ctrl+J)", "#4F46E5", "#4338CA", self.export_json)
        self.export_txt_button = self._make_action_button(actions_card, 6, "Exportar TXT (Ctrl+T)", "#2563EB", "#1D4ED8", self.export_txt)
        self.export_csv_button = self._make_action_button(actions_card, 7, "Exportar CSV (Ctrl+E)", "#0F766E", "#115E59", self.export_csv)
        self.video_button = self._make_action_button(actions_card, 8, "Generar video final (Ctrl+M)", "#B45309", "#92400E", self.generate_video)
        self.folder_button = self._make_action_button(actions_card, 9, "Abrir carpeta (Ctrl+O)", "#334155", "#1E293B", self.open_output_folder)
        self.exit_button = self._make_action_button(actions_card, 10, "Salir (Ctrl+Q)", "#7F1D1D", "#7C2D12", self._on_close)

    def _build_main_panel(self) -> None:
        header = ctk.CTkFrame(self.main_panel, fg_color=THEME["surface"], corner_radius=24)
        header.grid(row=0, column=0, sticky="ew", padx=18, pady=(18, 12))
        header.grid_columnconfigure(0, weight=1)
        header.grid_columnconfigure(1, weight=0)

        left = ctk.CTkFrame(header, fg_color="transparent")
        left.grid(row=0, column=0, sticky="ew", padx=18, pady=18)
        ctk.CTkLabel(
            left,
            text="Video workshop",
            text_color=THEME["primary_text"],
            font=ctk.CTkFont("Segoe UI Variable Display", 34, weight="bold"),
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            left,
            text="Use Quick setup for one-click video creation, or keep the separate script and render steps when you need more control.",
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
            text="LM Studio: not tested",
            text_color=ui_color("#BFDBFE", "#7DD3FC"),
            font=ctk.CTkFont("Segoe UI", 14, weight="bold"),
        )
        self.connection_chip.grid(row=0, column=0, sticky="w", padx=18, pady=12)
        self.render_chip = ctk.CTkLabel(
            status_strip,
            text=f"Video backend: {self.video_provider_var.get()}",
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
            text="Progreso del video: en espera",
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

        self.summary_tab = self.tab_view.add("Resumen")
        self.scenes_tab = self.tab_view.add("Escenas")
        self.json_tab = self.tab_view.add("JSON")
        self.history_tab = self.tab_view.add("Historial")

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
            text="Saved projects",
            text_color=THEME["primary_text"],
            font=ctk.CTkFont("Segoe UI", 18, weight="bold"),
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(
            history_header,
            text="Refresh",
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
        textbox.insert("1.0", "No project loaded yet.")
        textbox.configure(state="disabled")
        return textbox

    def _on_temperature_change(self, value: float) -> None:
        self.temperature_value_label.configure(text=f"{value:.2f}")
        self._schedule_save()

    def _on_appearance_change(self) -> None:
        mode = self._appearance_label_to_mode(self.appearance_mode_var.get())
        self._apply_user_appearance_mode(mode)
        self._schedule_save()
        self._set_status(f"Tema actualizado: {self.appearance_mode_var.get()}")

    def toggle_dark_mode(self) -> None:
        current = self._appearance_label_to_mode(self.appearance_mode_var.get())
        new_mode = "light" if current == "dark" else "dark"
        self.appearance_mode_var.set(self._appearance_mode_to_label(new_mode))
        self._on_appearance_change()

    def toggle_api_key_visibility(self) -> None:
        show = not self.show_api_key_var.get()
        self.show_api_key_var.set(show)
        self.api_key_entry.configure(show="" if show else "*")
        self.show_api_key_button.configure(text="Ocultar" if show else "Mostrar")
        self._reset_auto_close_timer()

    def _on_video_provider_change(self) -> None:
        self._sync_video_provider_ui()
        self._schedule_save()
        self._set_status(f"Render backend updated: {self.video_provider_var.get()}")

    def _sync_video_provider_ui(self) -> None:
        provider = self.video_provider_var.get().strip() or "Storyboard local"
        self.render_chip.configure(text=f"Video backend: {provider}")
        is_local_ai = provider == "Local AI video"
        if hasattr(self, "local_ai_card"):
            if is_local_ai:
                self.local_ai_card.grid()
            else:
                self.local_ai_card.grid_remove()
        if hasattr(self, "local_video_button"):
            self.local_video_button.configure(state="normal" if is_local_ai and not self.is_busy else "disabled")
        self._sync_tts_ui()

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
        self._set_status(f"Unhandled error: {value}", error=True)

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
            comfyui_checkpoint=self.comfyui_checkpoint_var.get().strip(),
            comfyui_workflow_path=self.comfyui_workflow_path_var.get().strip(),
            comfyui_negative_prompt=self.comfyui_negative_prompt_var.get().strip(),
            comfyui_poll_interval_seconds=self._safe_positive_int(self.comfyui_poll_interval_var.get(), 2),
            tts_backend=self.tts_backend_var.get().strip() or "Windows local",
            ffmpeg_path=self.ffmpeg_path_var.get().strip(),
            piper_executable_path=self.piper_executable_path_var.get().strip(),
            piper_model_path=self.piper_model_path_var.get().strip(),
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
            raise ValueError("Enter a topic or idea before generating.")
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

    def _build_video_render_request(self) -> VideoRenderRequest:
        if not self.current_project:
            raise ValueError("Generate or load a project before creating the final video.")
        return self._build_video_render_request_for_project(self.current_project)

    def _capture_video_render_settings(self) -> dict[str, Any]:
        workflow_path = self.comfyui_workflow_path_var.get().strip()
        checkpoint = self.comfyui_checkpoint_var.get().strip()
        if not workflow_path and checkpoint:
            try:
                generated = self.setup_manager.ensure_default_workflow(
                    checkpoint_name=checkpoint,
                    aspect_ratio=self.video_aspect_ratio_var.get().strip() or "9:16",
                )
                workflow_path = str(generated)
                self.comfyui_workflow_path_var.set(workflow_path)
            except Exception:
                workflow_path = ""
        ffmpeg_path = self.ffmpeg_path_var.get().strip() or self.setup_manager.resolve_ffmpeg_path(self.ffmpeg_path_var.get())
        if ffmpeg_path and ffmpeg_path != self.ffmpeg_path_var.get().strip():
            self.ffmpeg_path_var.set(ffmpeg_path)
        return {
            "output_dir": self.config_manager.resolve_output_dir(),
            "provider": self.video_provider_var.get().strip() or "Storyboard local",
            "aspect_ratio": self.video_aspect_ratio_var.get().strip() or "9:16",
            "request_timeout_seconds": self._safe_positive_int(self.timeout_var.get(), 180),
            "render_captions": bool(self.render_captions_var.get()),
            "comfyui_base_url": self.comfyui_base_url_var.get().strip() or "http://127.0.0.1:8188",
            "comfyui_worker_urls": self.comfyui_worker_urls_var.get().strip(),
            "parallel_scene_workers": self._safe_positive_int(self.parallel_scene_workers_var.get(), 1),
            "comfyui_checkpoint": checkpoint,
            "comfyui_workflow_path": workflow_path,
            "comfyui_negative_prompt": self.comfyui_negative_prompt_var.get().strip(),
            "comfyui_poll_interval_seconds": self._safe_positive_int(self.comfyui_poll_interval_var.get(), 2),
            "tts_backend": self.tts_backend_var.get().strip() or "Windows local",
            "ffmpeg_path": ffmpeg_path,
            "piper_executable_path": self.piper_executable_path_var.get().strip(),
            "piper_model_path": self.piper_model_path_var.get().strip(),
        }

    def _build_video_render_request_for_project(self, project: VideoProject, settings: dict[str, Any] | None = None) -> VideoRenderRequest:
        options = settings or self._capture_video_render_settings()
        return VideoRenderRequest(
            project=project,
            output_dir=options["output_dir"],
            provider=options["provider"],
            aspect_ratio=options["aspect_ratio"],
            request_timeout_seconds=options["request_timeout_seconds"],
            render_captions=options["render_captions"],
            comfyui_base_url=options["comfyui_base_url"],
            comfyui_worker_urls=options["comfyui_worker_urls"],
            parallel_scene_workers=options["parallel_scene_workers"],
            comfyui_checkpoint=options["comfyui_checkpoint"],
            comfyui_workflow_path=options["comfyui_workflow_path"],
            comfyui_negative_prompt=options["comfyui_negative_prompt"],
            comfyui_poll_interval_seconds=options["comfyui_poll_interval_seconds"],
            tts_backend=options["tts_backend"],
            ffmpeg_path=options["ffmpeg_path"],
            piper_executable_path=options["piper_executable_path"],
            piper_model_path=options["piper_model_path"],
        )

    def _build_runtime_config_snapshot(self, render_settings: dict[str, Any]) -> AppConfig:
        return replace(
            self.app_config,
            lmstudio_base_url=self.base_url_var.get().strip() or "http://127.0.0.1:1234",
            model=self.model_var.get().strip(),
            video_provider=render_settings["provider"],
            video_aspect_ratio=render_settings["aspect_ratio"],
            render_captions=bool(render_settings["render_captions"]),
            comfyui_base_url=render_settings["comfyui_base_url"],
            comfyui_worker_urls=render_settings["comfyui_worker_urls"],
            parallel_scene_workers=self._safe_positive_int(str(render_settings["parallel_scene_workers"]), 1),
            comfyui_checkpoint=render_settings["comfyui_checkpoint"],
            comfyui_workflow_path=render_settings["comfyui_workflow_path"],
            comfyui_negative_prompt=render_settings["comfyui_negative_prompt"],
            comfyui_poll_interval_seconds=self._safe_positive_int(str(render_settings["comfyui_poll_interval_seconds"]), 2),
            tts_backend=render_settings["tts_backend"],
            ffmpeg_path=render_settings["ffmpeg_path"],
            piper_executable_path=render_settings["piper_executable_path"],
            piper_model_path=render_settings["piper_model_path"],
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
            "comfyui_checkpoint": "comfyui_checkpoint",
            "comfyui_workflow_path": "comfyui_workflow_path",
            "comfyui_negative_prompt": "comfyui_negative_prompt",
            "comfyui_poll_interval_seconds": "comfyui_poll_interval_seconds",
            "tts_backend": "tts_backend",
            "piper_executable_path": "piper_executable_path",
            "piper_model_path": "piper_model_path",
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
                self._queue_event("progress", value=0.06, message="Opening LM Studio automatically...")
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
                message="FFmpeg prepared automatically.",
            )

        if render_settings["provider"] != "Local AI video":
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

        checkpoints = list(prep_result.status.checkpoints)
        local_ai_ready = prep_result.status.comfyui_reachable and bool(checkpoints or updates.get("comfyui_checkpoint"))

        if not local_ai_ready:
            self.setup_manager.ensure_package_installed(COMFYUI_PACKAGE_ID, install_missing=True)
            if self.setup_manager.launch_application("comfyui"):
                self._queue_event("progress", value=0.19, message="Opening ComfyUI automatically...")
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
        if not local_ai_ready or not workflow_path:
            updates["video_provider"] = "Storyboard local"
            self._persist_runtime_updates(
                render_settings,
                updates,
                message="ComfyUI was not ready. Falling back to Storyboard local automatically.",
                checkpoints=checkpoints,
            )
            return render_settings

        self._persist_runtime_updates(
            render_settings,
            updates,
            message="Environment prepared automatically for full video generation.",
            checkpoints=checkpoints,
        )
        return render_settings

    def _queue_event(self, event_type: str, **payload: Any) -> None:
        self.task_queue.put({"type": event_type, **payload})

    def _run_in_background(self, label: str, worker: Callable[[], None]) -> None:
        if self.is_busy:
            self._set_status("A task is already running. Please wait.", error=True)
            return
        self.is_busy = True
        self._toggle_busy_state(True)
        self._set_status(f"{label}...")
        self._set_progress_ui(0.02, label)

        def runner() -> None:
            try:
                worker()
            except Exception as exc:
                self.logger.exception("Background task failed: %s", exc)
                self._queue_event("error", message=str(exc))
            finally:
                self._queue_event("done")

        threading.Thread(target=runner, daemon=True).start()

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
                    self._set_progress_ui(0, "Progreso del video: error")
                    self._set_status(str(event.get("message", "Unknown error")), error=True)
                elif event_type == "connection":
                    models = event.get("models", [])
                    message = str(event.get("message", ""))
                    self.connection_chip.configure(text=f"LM Studio: {message}")
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
                    self.render_chip.configure(text=f"ComfyUI: {message}")
                    self._set_status(message)
                elif event_type == "environment":
                    summary = str(event.get("summary", ""))
                    checkpoint_values = event.get("checkpoints", [])
                    updates = event.get("updates", {})
                    if isinstance(updates, dict):
                        if "comfyui_base_url" in updates:
                            self.comfyui_base_url_var.set(str(updates.get("comfyui_base_url") or ""))
                        if "comfyui_worker_urls" in updates:
                            self.comfyui_worker_urls_var.set(str(updates.get("comfyui_worker_urls") or ""))
                        if "parallel_scene_workers" in updates:
                            self.parallel_scene_workers_var.set(str(updates.get("parallel_scene_workers") or "1"))
                        if "ffmpeg_path" in updates:
                            self.ffmpeg_path_var.set(str(updates.get("ffmpeg_path") or ""))
                        if "comfyui_checkpoint" in updates:
                            self.comfyui_checkpoint_var.set(str(updates.get("comfyui_checkpoint") or ""))
                        if "comfyui_workflow_path" in updates:
                            self.comfyui_workflow_path_var.set(str(updates.get("comfyui_workflow_path") or ""))
                        if "video_provider" in updates:
                            self.video_provider_var.set(str(updates.get("video_provider") or "Storyboard local"))
                        if "tts_backend" in updates:
                            self.tts_backend_var.set(str(updates.get("tts_backend") or "Windows local"))
                        if "comfyui_negative_prompt" in updates and not self.comfyui_negative_prompt_var.get().strip():
                            self.comfyui_negative_prompt_var.set(str(updates.get("comfyui_negative_prompt") or ""))
                    if checkpoint_values and hasattr(self, "comfyui_checkpoint_combo"):
                        self.comfyui_checkpoint_combo.configure(values=checkpoint_values)
                    if summary and hasattr(self, "setup_summary_label"):
                        self.setup_summary_label.configure(text=summary)
                    self._sync_video_provider_ui()
                    self._set_status(str(event.get("message", summary or "Environment updated.")), success=bool(event.get("success", False)))
                elif event_type == "project":
                    self.current_project = event["project"]
                    self.current_history_path = event.get("history_path")
                    self._render_project(self.current_project)
                    self._load_history_buttons()
                    if bool(event.get("finished", True)):
                        self._set_progress_ui(1, "Proyecto generado.")
                        self._set_status("Project generated and saved to history.", success=True)
                    else:
                        self._set_status("Proyecto generado. Iniciando render del video...")
                elif event_type == "video":
                    self._set_progress_ui(1, "Video final completado.")
                    result = event["result"]
                    destination = result.file_path or result.remote_video_url or result.remote_video_id
                    workers_used = result.metadata.get("workers_used")
                    if workers_used:
                        self.render_chip.configure(text=f"Video backend: {result.provider} | Workers: {workers_used}")
                    else:
                        self.render_chip.configure(text=f"Video backend: {result.provider}")
                    self._set_status(f"Video generated with {result.provider}: {destination}", success=True)
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
            self.progress_detail_label.configure(text=f"Progreso del video: {detail}")

    def _render_project(self, project: VideoProject) -> None:
        summary_lines = [
            f"Title: {project.title}",
            f"Summary: {project.summary}",
            f"General script: {project.general_script}",
            f"Structure: {project.structure}",
            f"Language: {project.output_language}",
            f"Mode: {project.generation_mode}",
            f"Topic: {project.source_topic}",
            f"Visual style: {project.visual_style}",
            f"Audience: {project.audience}",
            f"Narrative tone: {project.narrative_tone}",
            f"Format: {project.video_format}",
            f"Estimated duration: {project.estimated_total_duration_seconds}s",
        ]
        scene_blocks = []
        for scene in project.scenes:
            scene_blocks.append(
                "\n".join(
                    [
                        f"Scene {scene.scene_number}: {scene.scene_title}",
                        f"Description: {scene.description}",
                        f"Visual description: {scene.visual_description or '[not requested]'}",
                        f"Visual prompt: {scene.visual_prompt or '[not requested]'}",
                        f"Narration: {scene.narration}",
                        f"Duration: {scene.duration_seconds}s",
                        f"Transition: {scene.transition}",
                    ]
                )
            )
        self._write_textbox(self.summary_text, "\n".join(summary_lines))
        self._write_textbox(self.scenes_text, "\n\n".join(scene_blocks))
        self._write_textbox(self.json_text, json.dumps(project.to_dict(), indent=2, ensure_ascii=False))
        self.tab_view.set("Resumen")

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

        self._run_in_background("Testing LM Studio connection", worker)

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

        self._run_in_background("Testing ComfyUI connection", worker)

    def inspect_environment(self) -> None:
        if self.is_busy:
            return

        def worker() -> None:
            status = self.setup_manager.inspect_environment(self.app_config)
            summary = "\n".join(status.summary_lines())
            self._queue_event(
                "environment",
                summary=summary,
                checkpoints=status.checkpoints,
                updates={
                    "comfyui_base_url": status.comfyui_base_url,
                    "comfyui_worker_urls": ", ".join(status.comfyui_worker_urls),
                    "parallel_scene_workers": max(1, len(status.comfyui_worker_urls)),
                },
                message="Environment analysis updated.",
                success=status.ffmpeg_ready or status.comfyui_reachable or status.lmstudio_installed,
            )

        self._run_in_background("Inspecting environment", worker)

    def prepare_environment(self) -> None:
        def worker() -> None:
            result = self.setup_manager.prepare_environment(
                self.app_config,
                install_missing=True,
                install_default_checkpoint=True,
                progress_callback=lambda value, message: self._queue_event("progress", value=value, message=message),
            )
            self.config_manager.update(**result.updates)
            self.app_config = self.config_manager.config
            self._queue_event(
                "environment",
                summary="\n".join(result.status.summary_lines()),
                checkpoints=result.status.checkpoints,
                updates=result.updates,
                message="Automatic setup completed.",
                success=result.status.ffmpeg_ready or result.status.workflow_ready or bool(result.status.comfyui_checkpoint),
            )

        self._run_in_background("Preparing environment automatically", worker)

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
            status = self.setup_manager.inspect_environment(self.app_config)
            self._queue_event(
                "environment",
                summary="\n".join(status.summary_lines()),
                checkpoints=status.checkpoints,
                updates=updates,
                message="Recommended checkpoint installed. Restart ComfyUI if it was already open.",
                success=True,
            )

        self._run_in_background("Installing recommended ComfyUI checkpoint", worker)

    def start_generation(self) -> None:
        try:
            client = self._build_client()
            request = self._build_request()
            retry_attempts = self._safe_positive_int(self.retries_var.get(), 3)
        except Exception as exc:
            self._set_status(str(exc), error=True)
            return

        def worker() -> None:
            self._queue_event("progress", value=0.05, message="Preparing generation request...")
            if not request.model:
                models = client.list_models()
                if not models:
                    raise ValueError("No model was selected and LM Studio returned no models.")
                request.model = models[0]
                self._queue_event("connection", models=models, message=f"Connected successfully. Using {models[0]}.")

            project = self.generator_service.generate(
                client=client,
                request=request,
                retry_attempts=retry_attempts,
                progress_callback=lambda value, message: self._queue_event("progress", value=value, message=message),
            )
            history_path = self.history_service.save(project)
            self._queue_event("project", project=project, history_path=history_path, finished=True)

        self._run_in_background("Generating project", worker)

    def generate_full_video(self) -> None:
        try:
            request = self._build_request()
            if request.generation_mode != "Proyecto completo":
                request.generation_mode = "Proyecto completo"
                self.mode_var.set("Proyecto completo")
                self._schedule_save()
            retry_attempts = self._safe_positive_int(self.retries_var.get(), 3)
            render_settings = self._capture_video_render_settings()
        except Exception as exc:
            self._set_status(str(exc), error=True)
            return

        def worker() -> None:
            self._queue_event("progress", value=0.03, message="Preparing full video generation...")
            try:
                render_settings_local = self._prepare_render_settings_for_full_video(dict(render_settings))
            except Exception as exc:
                self.logger.warning("Automatic environment preparation failed, falling back to Storyboard local: %s", exc)
                render_settings_local = dict(render_settings)
                render_settings_local["provider"] = "Storyboard local"
                ffmpeg_path = self.setup_manager.ensure_ffmpeg_ready(render_settings_local["ffmpeg_path"], install_missing=True)
                if ffmpeg_path:
                    self._persist_runtime_updates(
                        render_settings_local,
                        {"ffmpeg_path": ffmpeg_path, "video_provider": "Storyboard local"},
                        message="Automatic setup had issues. Continuing with Storyboard local.",
                    )
                self._queue_event("progress", value=0.2, message="Automatic setup had issues. Continuing with Storyboard local...")

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
                        message="LM Studio was not ready in time. Building the project locally...",
                    )
                    project = self.generator_service.generate_fallback_project(request)
            else:
                self._queue_event(
                    "progress",
                    value=0.24,
                    message="LM Studio is unavailable. Building the project locally...",
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
            except Exception as exc:
                if render_request.provider != "Local AI video":
                    raise
                self.logger.warning("Local AI render failed, falling back to Storyboard local: %s", exc)
                self._queue_event(
                    "progress",
                    value=0.62,
                    message="Local AI render failed. Continuing with Storyboard local...",
                )
                render_settings_local["provider"] = "Storyboard local"
                ffmpeg_path = self.setup_manager.ensure_ffmpeg_ready(render_settings_local["ffmpeg_path"], install_missing=True)
                if ffmpeg_path:
                    render_settings_local["ffmpeg_path"] = ffmpeg_path
                    self._persist_runtime_updates(
                        render_settings_local,
                        {"ffmpeg_path": ffmpeg_path, "video_provider": "Storyboard local"},
                        message="Local AI render failed. Storyboard local was selected automatically.",
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

        self._run_in_background("Generating full video", worker)

    def export_json(self) -> None:
        if not self.current_project:
            self._set_status("Nothing to export yet.", error=True)
            return
        file_path = self.export_service.export_json(self.current_project, self.config_manager.resolve_output_dir())
        self._set_status(f"JSON exported: {file_path}", success=True)

    def export_txt(self) -> None:
        if not self.current_project:
            self._set_status("Nothing to export yet.", error=True)
            return
        file_path = self.export_service.export_txt(self.current_project, self.config_manager.resolve_output_dir())
        self._set_status(f"TXT exported: {file_path}", success=True)

    def export_csv(self) -> None:
        if not self.current_project:
            self._set_status("Nothing to export yet.", error=True)
            return
        file_path = self.export_service.export_csv(self.current_project, self.config_manager.resolve_output_dir())
        self._set_status(f"CSV exported: {file_path}", success=True)

    def generate_video(self) -> None:
        try:
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

        self._run_in_background(f"Generating final video with {render_request.provider}", worker)

    def open_output_folder(self) -> None:
        output_path = Path(self.config_manager.resolve_output_dir())
        output_path.mkdir(parents=True, exist_ok=True)
        os.startfile(output_path)  # type: ignore[attr-defined]
        self._set_status(f"Opened output folder: {output_path}")

    def launch_lmstudio(self) -> None:
        if self.setup_manager.launch_application("lmstudio"):
            self._set_status("LM Studio opened.")
            return
        self._set_status("LM Studio was not found. Use 'Preparar entorno automatico' first.", error=True)

    def launch_comfyui(self) -> None:
        if self.setup_manager.launch_application("comfyui"):
            self._set_status("ComfyUI opened.")
            return
        self._set_status("ComfyUI was not found. Use 'Preparar entorno automatico' first.", error=True)

    def open_comfyui_models_folder(self) -> None:
        folder = self.setup_manager.open_models_folder()
        self._set_status(f"Opened shared ComfyUI models folder: {folder}")

    def choose_output_folder(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.config_manager.resolve_output_dir())
        if selected:
            self.output_dir_var.set(selected)
            self._save_gui_state()
            self._set_status(f"Output folder updated: {selected}", success=True)

    def choose_comfyui_workflow(self) -> None:
        selected = filedialog.askopenfilename(
            title="Select ComfyUI workflow JSON",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            initialdir=APP_ROOT,
        )
        if selected:
            self.comfyui_workflow_path_var.set(selected)
            self._save_gui_state()
            self._set_status(f"ComfyUI workflow updated: {selected}", success=True)

    def choose_piper_model(self) -> None:
        selected = filedialog.askopenfilename(
            title="Select Piper model",
            filetypes=[("ONNX files", "*.onnx"), ("All files", "*.*")],
            initialdir=APP_ROOT,
        )
        if selected:
            self.piper_model_path_var.set(selected)
            self._save_gui_state()
            self._set_status(f"Piper model updated: {selected}", success=True)

    def _load_history_buttons(self) -> None:
        for child in self.history_scroll.winfo_children():
            child.destroy()

        entries = self.history_service.list_entries(limit=self.app_config.history_limit)
        if not entries:
            ctk.CTkLabel(
                self.history_scroll,
                text="No saved projects yet.",
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
            self._set_status(f"Failed to load history entry: {exc}", error=True)
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
        self._set_status(f"Loaded history project: {entry.file_path.name}", success=True)

    def show_about_dialog(self) -> None:
        dialog = ctk.CTkToplevel(self)
        dialog.title("About")
        dialog.geometry("500x190")
        dialog.resizable(False, False)
        dialog.configure(fg_color=THEME["status_bar"])
        dialog.transient(self)
        dialog.grab_set()

        year = datetime.now().year
        text = f"{DISPLAY_VERSION} creado por Synyster Rick, {year} Derechos Reservados"
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
            text="Cerrar",
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
        self._set_status("Project form reset.")

    def _reset_auto_close_timer(self) -> None:
        self._auto_close_remaining = max(1, self._safe_positive_int(self.auto_close_seconds_var.get(), 60))
        self._update_countdown_label()

    def _update_countdown_label(self) -> None:
        if not self.auto_close_var.get():
            self.countdown_label.configure(text=f"{DISPLAY_VERSION} | Auto-close off")
            return
        if self.is_busy:
            self.countdown_label.configure(text=f"{DISPLAY_VERSION} | Auto-close paused while processing")
            return
        self.countdown_label.configure(text=f"{DISPLAY_VERSION} | Auto-close in {self._auto_close_remaining}s")

    def _tick_auto_close(self) -> None:
        if self._closing or not self.winfo_exists():
            return
        if self.auto_close_var.get() and not self.is_busy:
            self._auto_close_remaining -= 1
            if self._auto_close_remaining <= 0:
                self._set_status("Auto-close timer reached zero. Closing application.")
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
