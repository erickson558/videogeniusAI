from __future__ import annotations

import logging
import tempfile
import unittest
import os
import subprocess
from pathlib import Path
from unittest import mock

from PIL import Image

from videogenius_ai.comfyui_client import ComfyUIClient, _replace_placeholders, detect_workflow_output_mode
from videogenius_ai.config import ConfigManager, sanitize_window_geometry
from videogenius_ai.export_service import ExportService
from videogenius_ai.generator_service import SceneGeneratorService
from videogenius_ai.gpu_detector import GPUDetector
from videogenius_ai.i18n import TranslationManager, normalize_ui_language
from videogenius_ai.lmstudio_client import sort_models_for_generation
from videogenius_ai.logging_utils import configure_logging
from videogenius_ai.prompt_director import build_cinematic_scene_prompt, build_scene_negative_prompt
from videogenius_ai.render_devices import (
    GPUDevice,
    RENDER_DEVICE_ALL,
    RENDER_DEVICE_CPU,
    RENDER_DEVICE_NVIDIA,
    VIDEO_ENCODER_NVENC_H264,
    VideoEncoderPlan,
    build_video_encoder_pool,
    detect_gpu_devices,
)
from videogenius_ai.setup_manager import SetupManager
from videogenius_ai.models import GenerationRequest, RenderedVideoResult, VideoProject
from videogenius_ai.video_render_service import VideoRenderService
from videogenius_ai.video_renderer import VideoRenderer
from videogenius_ai.video_service import StoryboardVideoService
from videogenius_ai.utils import brief_requests_silent_narration, parse_json_payload


class JsonParsingTests(unittest.TestCase):
    def test_parse_json_from_markdown_fence(self) -> None:
        raw = """```json\n{"title":"Demo","scenes":[{"scene_number":1}]}\n```"""
        payload = parse_json_payload(raw)
        self.assertEqual(payload["title"], "Demo")

    def test_parse_json_after_reasoning_block(self) -> None:
        raw = """<think>
Need to plan the answer first.
</think>
{"title":"Demo","scenes":[{"scene_number":1}]}"""
        payload = parse_json_payload(raw)
        self.assertEqual(payload["title"], "Demo")

    def test_parse_json_after_unclosed_reasoning_prefix(self) -> None:
        raw = """<think>
I should think first.
{"title":"Demo","scenes":[{"scene_number":1}]}"""
        payload = parse_json_payload(raw)
        self.assertEqual(payload["title"], "Demo")

    def test_parse_json_repairs_unquoted_keys_and_trailing_commas(self) -> None:
        raw = """{
title: "Demo",
scenes: [
  {"scene_number": 1,},
],
}"""
        payload = parse_json_payload(raw)
        self.assertEqual(payload["title"], "Demo")
        self.assertEqual(payload["scenes"][0]["scene_number"], 1)

    def test_parse_json_repairs_control_characters_inside_strings(self) -> None:
        raw = '{"title":"Demo","summary":"Linea 1\nLinea 2","scenes":[{"scene_number":1}]}'
        payload = parse_json_payload(raw)
        self.assertEqual(payload["summary"], "Linea 1\nLinea 2")


class BriefIntentTests(unittest.TestCase):
    def test_brief_requests_silent_narration_detects_spanish_and_english_variants(self) -> None:
        self.assertTrue(brief_requests_silent_narration("Genera un video fantastico sin narración sobre IA"))
        self.assertTrue(brief_requests_silent_narration("Create a cinematic short without narration or voiceover"))
        self.assertFalse(brief_requests_silent_narration("Create a narrated video about AI"))


class LMStudioModelSelectionTests(unittest.TestCase):
    def test_sort_models_for_generation_prefers_chat_models_over_reasoning_and_embeddings(self) -> None:
        models = [
            "text-embedding-nomic-embed-text-v1.5",
            "copilot-codellama-7b.gguf",
            "deepseek/deepseek-r1-0528-qwen3-8b",
            "google/gemma-3-4b",
            "openai/gpt-oss-20b",
        ]
        ordered = sort_models_for_generation(models)
        self.assertLess(ordered.index("google/gemma-3-4b"), ordered.index("copilot-codellama-7b.gguf"))
        self.assertLess(ordered.index("google/gemma-3-4b"), ordered.index("deepseek/deepseek-r1-0528-qwen3-8b"))
        self.assertLess(ordered.index("openai/gpt-oss-20b"), ordered.index("text-embedding-nomic-embed-text-v1.5"))
        self.assertEqual(ordered[-1], "text-embedding-nomic-embed-text-v1.5")


class GenerationNormalizationTests(unittest.TestCase):
    def test_normalize_project_fills_expected_fields(self) -> None:
        service = SceneGeneratorService()
        request = GenerationRequest(
            topic="Test project",
            visual_style="Cyberpunk",
            audience="General",
            narrative_tone="Epic",
            video_format="YouTube Short",
            output_language="Espanol",
            total_duration_seconds=30,
            scene_count=3,
            generation_mode="Proyecto completo",
            model="model",
            temperature=0.7,
            max_tokens=1000,
        )
        payload = {
            "title": "Demo video",
            "summary": "Summary",
            "general_script": "Script",
            "structure": "Hook, middle, close",
            "scenes": [
                {"scene_number": 1, "scene_title": "One", "description": "Desc 1", "narration": "Narr 1", "duration_seconds": 10},
                {"scene_number": 2, "scene_title": "Two", "description": "Desc 2", "narration": "Narr 2", "duration_seconds": 10},
                {"scene_number": 3, "scene_title": "Three", "description": "Desc 3", "narration": "Narr 3", "duration_seconds": 10},
            ],
        }
        project = service.normalize_project(payload, request, raw_response="{}")
        self.assertEqual(project.title, "Demo video")
        self.assertEqual(len(project.scenes), 3)
        self.assertEqual(sum(scene.duration_seconds for scene in project.scenes), 30)

    def test_generate_fallback_project_respects_scene_count_and_duration(self) -> None:
        service = SceneGeneratorService()
        request = GenerationRequest(
            topic="Historia breve sobre robots en una ciudad futurista",
            visual_style="Cyberpunk",
            audience="General",
            narrative_tone="Epic",
            video_format="YouTube Short",
            output_language="Espanol",
            total_duration_seconds=30,
            scene_count=3,
            generation_mode="Proyecto completo",
            model="",
            temperature=0.7,
            max_tokens=1000,
        )
        project = service.generate_fallback_project(request)
        self.assertEqual(len(project.scenes), 3)
        self.assertEqual(sum(scene.duration_seconds for scene in project.scenes), 30)
        self.assertIn("Historia breve sobre robots", project.title)
        self.assertTrue(all(scene.shots for scene in project.scenes))
        self.assertTrue(all(scene.cinematic_intent for scene in project.scenes))

    def test_generate_fallback_project_uses_brief_focus_instead_of_full_metaprompt(self) -> None:
        service = SceneGeneratorService()
        request = GenerationRequest(
            topic="5 videos de IA\nGenerate a unique YouTube Shorts video every time this prompt is run.\nUse different styles.",
            visual_style="Cyberpunk",
            audience="General",
            narrative_tone="Epic",
            video_format="YouTube Short",
            output_language="Espanol",
            total_duration_seconds=30,
            scene_count=3,
            generation_mode="Proyecto completo",
            model="",
            temperature=0.7,
            max_tokens=1000,
        )
        project = service.generate_fallback_project(request)
        self.assertTrue(project.title)
        self.assertNotIn("Generate a unique YouTube Shorts video", project.title)
        self.assertNotIn("every time this prompt is run", project.summary)

    def test_generate_fallback_project_can_pick_theme_from_brief_options(self) -> None:
        service = SceneGeneratorService()
        request = GenerationRequest(
            topic="Generate a unique YouTube Shorts video.\n- Vary the theme randomly: nature, technology, travel.\n- Use a different visual style each time: anime, watercolor, claymation.",
            visual_style="Cyberpunk",
            audience="General",
            narrative_tone="Epic",
            video_format="YouTube Short",
            output_language="Espanol",
            total_duration_seconds=30,
            scene_count=3,
            generation_mode="Proyecto completo",
            model="",
            temperature=0.7,
            max_tokens=1000,
        )
        with mock.patch.object(service, "_pick_brief_option", side_effect=["technology", "anime"]):
            project = service.generate_fallback_project(request)
        self.assertIn("technology", project.title.lower())
        self.assertIn("anime", project.scenes[0].visual_prompt.lower())

    def test_generate_fallback_project_respects_no_narration_brief(self) -> None:
        service = SceneGeneratorService()
        request = GenerationRequest(
            topic="Genera un video fantastico sobre IA sin narración",
            visual_style="Cyberpunk",
            audience="General",
            narrative_tone="Epic",
            video_format="YouTube Short",
            output_language="Espanol",
            total_duration_seconds=20,
            scene_count=2,
            generation_mode="Proyecto completo",
            model="",
            temperature=0.7,
            max_tokens=1000,
        )
        project = service.generate_fallback_project(request)
        self.assertTrue(all(not scene.narration for scene in project.scenes))

    def test_normalize_project_preserves_shot_plan_and_direction_fields(self) -> None:
        service = SceneGeneratorService()
        request = GenerationRequest(
            topic="Tormenta electrica sobre una metropolis futurista",
            visual_style="Electric cyberpunk",
            audience="General",
            narrative_tone="Epic",
            video_format="YouTube Short",
            output_language="Espanol",
            total_duration_seconds=12,
            scene_count=1,
            generation_mode="Proyecto completo",
            model="model",
            temperature=0.7,
            max_tokens=1000,
        )
        project = service.normalize_project(
            {
                "title": "Electric city",
                "summary": "Summary",
                "general_script": "Script",
                "structure": "Hook / payoff",
                "scenes": [
                    {
                        "scene_number": 1,
                        "scene_title": "Arrival",
                        "description": "La ciudad despierta",
                        "visual_description": "Torres y rayos",
                        "visual_prompt": "Epic electric skyline",
                        "narration": "La energia corre por toda la ciudad.",
                        "duration_seconds": 12,
                        "transition": "Flash cut",
                        "cinematic_intent": "hacer que la ciudad se sienta enorme",
                        "camera_language": "wide lens and parallax",
                        "lighting_style": "neon storm light",
                        "color_palette": "electric blue and amber",
                        "energy_level": "charged",
                        "negative_prompt": "boring sky",
                        "shots": [
                            {
                                "shot_number": 1,
                                "duration_seconds": 4,
                                "shot_type": "establishing shot",
                                "camera_angle": "low angle",
                                "camera_motion": "slow push-in",
                                "focal_subject": "torres electricas",
                                "action": "rayos recorriendo el cielo",
                                "environment": "metropolis nocturna",
                                "lighting": "storm glow",
                                "mood": "awe",
                                "color_palette": "electric blue",
                                "visual_prompt": "wide skyline with lightning",
                            },
                            {
                                "shot_number": 2,
                                "duration_seconds": 8,
                                "shot_type": "detail shot",
                                "camera_angle": "close perspective",
                                "camera_motion": "micro orbit",
                                "focal_subject": "cables y neon",
                                "action": "energia fluyendo",
                                "environment": "calle lluviosa",
                                "lighting": "neon reflections",
                                "mood": "kinetic",
                                "color_palette": "cyan and magenta",
                                "visual_prompt": "electric street detail",
                            },
                        ],
                    }
                ],
            },
            request,
            raw_response="{}",
        )
        scene = project.scenes[0]
        self.assertEqual(scene.cinematic_intent, "hacer que la ciudad se sienta enorme")
        self.assertEqual(scene.camera_language, "wide lens and parallax")
        self.assertEqual(scene.lighting_style, "neon storm light")
        self.assertEqual(scene.color_palette, "electric blue and amber")
        self.assertEqual(scene.energy_level, "charged")
        self.assertEqual(scene.negative_prompt, "boring sky")
        self.assertEqual(len(scene.shots), 2)
        self.assertEqual(scene.shots[0].camera_motion, "slow push-in")
        self.assertAlmostEqual(sum(shot.duration_seconds for shot in scene.shots), 12.0, places=1)

    def test_normalize_project_clears_narration_when_brief_requests_silent_output(self) -> None:
        service = SceneGeneratorService()
        request = GenerationRequest(
            topic="Video de ciencia ficcion sin narracion",
            visual_style="Cinematic",
            audience="General",
            narrative_tone="Epic",
            video_format="YouTube Short",
            output_language="Espanol",
            total_duration_seconds=10,
            scene_count=1,
            generation_mode="Proyecto completo",
            model="model",
            temperature=0.7,
            max_tokens=1000,
        )
        project = service.normalize_project(
            {
                "title": "Demo",
                "summary": "Summary",
                "general_script": "Script",
                "structure": "Hook",
                "scenes": [
                    {"scene_number": 1, "scene_title": "One", "description": "Desc 1", "narration": "Esta voz no deberia quedar", "duration_seconds": 10},
                ],
            },
            request,
            raw_response="{}",
        )
        self.assertEqual(project.scenes[0].narration, "")


class ExportTests(unittest.TestCase):
    def test_export_json_txt_csv(self) -> None:
        service = SceneGeneratorService()
        request = GenerationRequest(
            topic="Export test",
            visual_style="Style",
            audience="General",
            narrative_tone="Tone",
            video_format="Reel",
            output_language="Espanol",
            total_duration_seconds=20,
            scene_count=2,
            generation_mode="Proyecto completo",
            model="model",
            temperature=0.5,
            max_tokens=800,
        )
        project = service.normalize_project(
            {
                "title": "Export video",
                "summary": "Summary",
                "general_script": "Script",
                "structure": "Intro / outro",
                "scenes": [
                    {"scene_number": 1, "scene_title": "One", "description": "Desc", "narration": "Narr", "duration_seconds": 10},
                    {"scene_number": 2, "scene_title": "Two", "description": "Desc", "narration": "Narr", "duration_seconds": 10},
                ],
            },
            request,
            raw_response="{}",
        )
        exporter = ExportService()
        with tempfile.TemporaryDirectory() as temp_dir:
            json_file = exporter.export_json(project, temp_dir)
            txt_file = exporter.export_txt(project, temp_dir)
            csv_file = exporter.export_csv(project, temp_dir)
            self.assertTrue(Path(json_file).exists())
            self.assertTrue(Path(txt_file).exists())
            self.assertTrue(Path(csv_file).exists())


class ConfigTests(unittest.TestCase):
    def test_config_persists_appearance_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            manager = ConfigManager(config_path=config_path)
            manager.update(
                ui_language="en",
                appearance_mode="dark",
                output_dir=str(Path(temp_dir) / "output"),
                video_provider="Local AI video",
                render_captions=False,
                comfyui_base_url="http://127.0.0.1:8188",
                comfyui_worker_urls="http://127.0.0.1:8188, http://127.0.0.1:8189",
                parallel_scene_workers=2,
                render_gpu_preference="GPU 1: NVIDIA RTX 4080",
                video_render_device_preference=RENDER_DEVICE_ALL,
                video_encoder_preference=VIDEO_ENCODER_NVENC_H264,
                avatar_source_image_path=str(Path(temp_dir) / "avatar.png"),
            )

            reloaded = ConfigManager(config_path=config_path)
            self.assertEqual(reloaded.config.ui_language, "en")
            self.assertEqual(reloaded.config.appearance_mode, "dark")
            self.assertEqual(reloaded.config.video_provider, "Local AI video")
            self.assertFalse(reloaded.config.render_captions)
            self.assertEqual(reloaded.config.comfyui_base_url, "http://127.0.0.1:8188")
            self.assertEqual(reloaded.config.comfyui_worker_urls, "http://127.0.0.1:8188, http://127.0.0.1:8189")
            self.assertEqual(reloaded.config.parallel_scene_workers, 2)
            self.assertEqual(reloaded.config.render_gpu_preference, "GPU 1: NVIDIA RTX 4080")
            self.assertEqual(reloaded.config.video_render_device_preference, RENDER_DEVICE_ALL)
            self.assertEqual(reloaded.config.video_encoder_preference, VIDEO_ENCODER_NVENC_H264)
            self.assertEqual(reloaded.config.avatar_source_image_path, str(Path(temp_dir) / "avatar.png"))

    def test_invalid_window_geometry_falls_back_to_default(self) -> None:
        self.assertEqual(sanitize_window_geometry("160x160+50+50"), "1460x900+80+40")
        self.assertEqual(sanitize_window_geometry("bad-value"), "1460x900+80+40")
        self.assertEqual(sanitize_window_geometry("1460x900+80+40"), "1460x900+80+40")


class InternationalizationTests(unittest.TestCase):
    def test_translation_manager_loads_expected_language(self) -> None:
        translator = TranslationManager("en")
        self.assertEqual(translator.translate("menu.file"), "File")
        self.assertEqual(translator.translate("buttons.generate_full_video"), "Generate full video (Ctrl+Shift+G)")

    def test_translation_manager_falls_back_and_formats_values(self) -> None:
        translator = TranslationManager("es")
        self.assertEqual(
            translator.translate("status.video_completed", provider="Storyboard local", destination="D:/video.mp4"),
            "Video generado con Storyboard local: D:/video.mp4",
        )
        self.assertEqual(translator.translate("missing.key"), "missing.key")

    def test_normalize_ui_language_defaults_to_spanish(self) -> None:
        self.assertEqual(normalize_ui_language("en-US"), "en")
        self.assertEqual(normalize_ui_language(""), "es")
        self.assertEqual(normalize_ui_language("fr"), "es")


class SetupManagerGpuTests(unittest.TestCase):
    def test_format_gpu_options_and_parse_selected_index(self) -> None:
        manager = SetupManager()
        options = manager.format_gpu_options(["NVIDIA RTX 4080", "NVIDIA RTX 4090"])
        self.assertEqual(options, ["Auto", "GPU 0: NVIDIA RTX 4080", "GPU 1: NVIDIA RTX 4090"])
        self.assertIsNone(manager.gpu_index_from_choice("Auto"))
        self.assertEqual(manager.gpu_index_from_choice("GPU 1: NVIDIA RTX 4090"), 1)
        self.assertIsNone(manager.gpu_index_from_choice("Invalid"))

    def test_format_video_render_options_include_cpu_and_multi_gpu_mode(self) -> None:
        manager = SetupManager()
        options = manager.format_video_render_options(["NVIDIA RTX 4080", "Intel Arc A770"])
        self.assertEqual(
            options,
            [
                "Auto",
                "CPU only",
                "NVIDIA (auto)",
                "Intel (auto)",
                "GPU 0: NVIDIA RTX 4080",
                "GPU 1: Intel Arc A770",
                "All GPUs (split scenes)",
            ],
        )

    def test_launch_application_sets_selected_gpu_environment_for_comfyui(self) -> None:
        manager = SetupManager()
        with (
            mock.patch.object(manager, "launch_comfyui_api_server", return_value=False),
            mock.patch.object(manager, "find_application_path", return_value=r"C:\Apps\ComfyUI.exe"),
            mock.patch("videogenius_ai.setup_manager.subprocess.Popen") as popen_mock,
        ):
            launched = manager.launch_application("comfyui", gpu_choice="GPU 1: NVIDIA RTX 4090")

        self.assertTrue(launched)
        popen_mock.assert_called_once()
        args, kwargs = popen_mock.call_args
        self.assertEqual(args[0], [r"C:\Apps\ComfyUI.exe"])
        self.assertEqual(kwargs["env"]["CUDA_DEVICE_ORDER"], "PCI_BUS_ID")
        self.assertEqual(kwargs["env"]["CUDA_VISIBLE_DEVICES"], "1")
        self.assertEqual(kwargs["env"]["HIP_VISIBLE_DEVICES"], "1")
        self.assertEqual(kwargs["env"]["ROCR_VISIBLE_DEVICES"], "1")


class RenderDeviceTests(unittest.TestCase):
    def test_detect_gpu_devices_keeps_duplicate_model_names(self) -> None:
        raw_payload = (
            '[{"Name":"NVIDIA RTX 4090","AdapterCompatibility":"NVIDIA"},'
            '{"Name":"NVIDIA RTX 4090","AdapterCompatibility":"NVIDIA"}]'
        )
        with mock.patch("videogenius_ai.render_devices._run", return_value=mock.Mock(stdout=raw_payload)):
            devices = detect_gpu_devices()
        self.assertEqual([device.index for device in devices], [0, 1])
        self.assertEqual([device.name for device in devices], ["NVIDIA RTX 4090", "NVIDIA RTX 4090"])

    def test_build_video_encoder_pool_resolves_multi_gpu_hardware_encoders(self) -> None:
        devices = [
            GPUDevice(index=0, name="NVIDIA RTX 4080", vendor="nvidia"),
            GPUDevice(index=1, name="Intel Arc A770", vendor="intel"),
        ]
        pool = build_video_encoder_pool(
            RENDER_DEVICE_ALL,
            devices,
            available_encoders={"h264_nvenc", "h264_qsv"},
        )
        self.assertEqual([plan.encoder_name for plan in pool], ["h264_nvenc", "h264_qsv"])
        self.assertTrue(all(plan.hardware_accelerated for plan in pool))

    def test_build_video_encoder_pool_honors_cpu_only_choice(self) -> None:
        devices = [GPUDevice(index=0, name="NVIDIA RTX 4080", vendor="nvidia")]
        pool = build_video_encoder_pool(
            RENDER_DEVICE_CPU,
            devices,
            available_encoders={"h264_nvenc"},
        )
        self.assertEqual(len(pool), 1)
        self.assertEqual(pool[0].encoder_name, "libx264")
        self.assertFalse(pool[0].hardware_accelerated)

    def test_build_video_encoder_pool_falls_back_to_cpu_for_unsupported_gpu(self) -> None:
        devices = [GPUDevice(index=0, name="Unknown Accelerator", vendor="unknown")]
        pool = build_video_encoder_pool(
            "GPU 0: Unknown Accelerator",
            devices,
            available_encoders=set(),
        )
        self.assertEqual(pool[0].encoder_name, "libx264")
        self.assertIn("CPU fallback", pool[0].label)

    def test_build_video_encoder_pool_supports_vendor_auto_choice_and_encoder_override(self) -> None:
        devices = [
            GPUDevice(index=0, name="NVIDIA RTX 4080", vendor="nvidia"),
            GPUDevice(index=1, name="Intel Arc A770", vendor="intel"),
        ]
        pool = build_video_encoder_pool(
            RENDER_DEVICE_NVIDIA,
            devices,
            available_encoders={"h264_nvenc", "h264_qsv"},
            encoder_preference=VIDEO_ENCODER_NVENC_H264,
        )
        self.assertEqual(len(pool), 1)
        self.assertEqual(pool[0].encoder_name, "h264_nvenc")
        self.assertEqual(pool[0].gpu_index, 0)


class GPUDetectorTests(unittest.TestCase):
    def test_detect_exposes_render_and_encoder_options(self) -> None:
        devices = [GPUDevice(index=0, name="NVIDIA RTX 4080", vendor="nvidia")]
        with (
            mock.patch("videogenius_ai.gpu_detector.detect_gpu_devices", return_value=devices),
            mock.patch("videogenius_ai.gpu_detector.detect_ffmpeg_video_encoders", return_value=("h264_nvenc", "libx264")),
        ):
            result = GPUDetector().detect("C:/ffmpeg.exe")
        self.assertIn("NVIDIA (auto)", result.render_options)
        self.assertIn("h264_nvenc", result.encoder_options)


class VideoRendererTests(unittest.TestCase):
    def test_run_with_fallback_retries_cpu_when_hardware_encoder_fails(self) -> None:
        ffmpeg_wrapper = mock.Mock()
        ffmpeg_wrapper.ffmpeg_path = "C:/ffmpeg.exe"
        ffmpeg_wrapper.run.side_effect = [
            subprocess.CalledProcessError(1, ["ffmpeg"], stderr=b"nvenc failed"),
            mock.Mock(),
        ]

        with (
            mock.patch("videogenius_ai.video_renderer.FFmpegWrapper", return_value=ffmpeg_wrapper),
            mock.patch("videogenius_ai.video_renderer.GPUDetector"),
        ):
            renderer = VideoRenderer("C:/ffmpeg.exe")

        gpu_plan = VideoEncoderPlan(
            label="GPU 0: NVIDIA RTX 4080",
            encoder_name="h264_nvenc",
            ffmpeg_args=("-c:v", "h264_nvenc"),
            vendor="nvidia",
            gpu_index=0,
            hardware_accelerated=True,
        )

        used_plan = renderer.run_with_fallback(
            lambda plan: ["ffmpeg", "-c:v", plan.encoder_name],
            gpu_plan,
            stage_label="unit-test",
        )

        self.assertEqual(used_plan.encoder_name, "libx264")
        self.assertEqual(ffmpeg_wrapper.run.call_count, 2)


class LoggingTests(unittest.TestCase):
    def test_configure_logging_writes_context_without_duplicate_handlers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "runtime" / "app.log"
            root_name = "videogenius_ai_test_logging"
            try:
                logger = configure_logging(
                    "worker",
                    log_path=log_path,
                    root_name=root_name,
                    reset=True,
                    install_exception_hooks=False,
                )
                logger.info("First message")
                logger_again = configure_logging(
                    "worker",
                    log_path=log_path,
                    root_name=root_name,
                    install_exception_hooks=False,
                )
                logger_again.warning("Second message")

                root_logger = logging.getLogger(root_name)
                for handler in root_logger.handlers:
                    handler.flush()

                content = log_path.read_text(encoding="utf-8")
                self.assertEqual(logger.name, f"{root_name}.worker")
                self.assertEqual(len(root_logger.handlers), 1)
                self.assertIn("First message", content)
                self.assertIn("Second message", content)
                self.assertIn(f"{root_name}.worker", content)
                self.assertIn("MainThread", content)
            finally:
                root_logger = logging.getLogger(root_name)
                for handler in list(root_logger.handlers):
                    root_logger.removeHandler(handler)
                    handler.close()


class LocalVideoSupportTests(unittest.TestCase):
    def _make_project(self):
        service = SceneGeneratorService()
        request = GenerationRequest(
            topic="Curiosidades del espacio",
            visual_style="Cinematic",
            audience="General",
            narrative_tone="Fast-paced",
            video_format="YouTube Short",
            output_language="Espanol",
            total_duration_seconds=30,
            scene_count=3,
            generation_mode="Proyecto completo",
            model="model",
            temperature=0.7,
            max_tokens=900,
        )
        return service.normalize_project(
            {
                "title": "Planetas raros",
                "summary": "Datos breves y llamativos",
                "general_script": "Hook, facts, close",
                "structure": "Hook / middle / payoff",
                "scenes": [
                    {"scene_number": 1, "scene_title": "Hook", "description": "Apertura", "visual_prompt": "Planeta gigante", "narration": "Hoy verás tres datos extraños del espacio.", "duration_seconds": 10},
                    {"scene_number": 2, "scene_title": "Fact 1", "description": "Hecho uno", "visual_prompt": "Júpiter y anillos", "narration": "Júpiter tiene un sistema de anillos tenue pero real.", "duration_seconds": 10},
                    {"scene_number": 3, "scene_title": "Close", "description": "Cierre", "visual_prompt": "Galaxia brillante", "narration": "Si quieres parte dos, deja tu comentario.", "duration_seconds": 10},
                ],
            },
            request,
            raw_response="{}",
        )

    def test_video_render_service_normalizes_unknown_provider(self) -> None:
        renderer = VideoRenderService(local_ai_service=None)
        request = self._make_project()
        # Reuse the project only to ensure service construction stays lazy.
        self.assertEqual(renderer._normalize_provider("Unknown backend"), "Storyboard local")
        self.assertEqual(renderer._normalize_provider("Local Avatar video"), "Local Avatar video")
        self.assertEqual(request.title, "Planetas raros")

    def test_placeholder_replacement_updates_nested_workflow(self) -> None:
        payload = {
            "1": {"inputs": {"text": "__PROMPT__"}},
            "2": {"inputs": {"negative": "__NEGATIVE_PROMPT__", "seed": "__SEED__"}},
        }
        replaced = _replace_placeholders(
            payload,
            {
                "__PROMPT__": "A futuristic city",
                "__NEGATIVE_PROMPT__": "blurry, low quality",
                "__SEED__": 12345,
            },
        )
        self.assertEqual(replaced["1"]["inputs"]["text"], "A futuristic city")
        self.assertEqual(replaced["2"]["inputs"]["negative"], "blurry, low quality")
        self.assertEqual(replaced["2"]["inputs"]["seed"], 12345)

    def test_prepare_workflow_accepts_extra_avatar_replacements(self) -> None:
        client = ComfyUIClient(base_url="http://127.0.0.1:8188")
        with tempfile.TemporaryDirectory() as temp_dir:
            workflow_path = Path(temp_dir) / "avatar_workflow.json"
            workflow_path.write_text(
                '{"1": {"inputs": {"image": "__SOURCE_IMAGE__", "audio": "__AUDIO_FILE__", "text": "__PROMPT__"}}}',
                encoding="utf-8",
            )
            prepared = client._prepare_workflow(
                workflow_path,
                prompt_text="Avatar prompt",
                negative_prompt="",
                output_prefix="avatar-scene",
                extra_replacements={
                    "__SOURCE_IMAGE__": "C:/avatar.png",
                    "__AUDIO_FILE__": "C:/audio.wav",
                },
            )
            self.assertEqual(prepared["1"]["inputs"]["image"], "C:/avatar.png")
            self.assertEqual(prepared["1"]["inputs"]["audio"], "C:/audio.wav")
            self.assertEqual(prepared["1"]["inputs"]["text"], "Avatar prompt")

    def test_default_workflow_contains_expected_placeholders(self) -> None:
        manager = SetupManager()
        payload = manager.build_default_workflow_payload(
            checkpoint_name="sdxl-demo.safetensors",
            aspect_ratio="9:16",
        )
        self.assertEqual(payload["4"]["inputs"]["ckpt_name"], "sdxl-demo.safetensors")
        self.assertEqual(payload["6"]["inputs"]["text"], "__PROMPT__")
        self.assertEqual(payload["7"]["inputs"]["text"], "__NEGATIVE_PROMPT__")
        self.assertEqual(payload["3"]["inputs"]["seed"], "__SEED__")
        self.assertEqual(payload["9"]["inputs"]["filename_prefix"], "__OUTPUT_PREFIX__")

    def test_default_avatar_workflow_contains_expected_placeholders(self) -> None:
        manager = SetupManager()
        payload = manager.build_default_avatar_workflow_payload(aspect_ratio="9:16")
        self.assertEqual(payload["1"]["class_type"], "Echo_LoadModel")
        self.assertEqual(payload["1"]["inputs"]["vae"], "sd-vae-ft-mse.safetensors")
        self.assertFalse(payload["1"]["inputs"]["lowvram"])
        self.assertEqual(payload["2"]["inputs"]["image"], "__AVATAR_IMAGE__")
        self.assertEqual(payload["3"]["inputs"]["audio_file"], "__AUDIO_FILE__")
        self.assertEqual(payload["4"]["inputs"]["prompt"], "__PROMPT__")
        self.assertEqual(payload["4"]["inputs"]["width"], "__WIDTH__")
        self.assertEqual(payload["4"]["inputs"]["length"], "__SCENE_FRAMES__")
        self.assertEqual(payload["6"]["class_type"], "VHS_VideoCombine")
        self.assertEqual(payload["6"]["inputs"]["filename_prefix"], "__OUTPUT_PREFIX__")

    def test_checkpoint_extraction_from_object_info_payload(self) -> None:
        client = ComfyUIClient(base_url="http://127.0.0.1:8188")
        checkpoints = client._extract_checkpoint_names(
            {
                "CheckpointLoaderSimple": {
                    "input": {
                        "required": {
                            "ckpt_name": [["model-a.safetensors", "model-b.safetensors"]],
                        }
                    }
                }
            }
        )
        self.assertEqual(checkpoints, ["model-a.safetensors", "model-b.safetensors"])

    def test_detect_workflow_output_mode_distinguishes_image_and_video_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "image_workflow.json"
            image_path.write_text(
                '{"1": {"class_type": "CheckpointLoaderSimple"}, "9": {"class_type": "SaveImage"}}',
                encoding="utf-8",
            )
            video_path = Path(temp_dir) / "video_workflow.json"
            video_path.write_text(
                '{"1": {"class_type": "CheckpointLoaderSimple"}, "9": {"class_type": "VHS_VideoCombine"}}',
                encoding="utf-8",
            )

            self.assertEqual(detect_workflow_output_mode(image_path), "image")
            self.assertEqual(detect_workflow_output_mode(video_path), "video")

    def test_storyboard_prompt_targets_vertical_short_visuals(self) -> None:
        service = StoryboardVideoService()
        project = self._make_project()
        prompt = service._scene_prompt(project, 0, "9:16")  # type: ignore[attr-defined]
        self.assertIn("vertical 9:16 cinematic composition", prompt)
        self.assertIn("no text overlay", prompt)
        self.assertIn("Planeta gigante", prompt)

    def test_cinematic_prompt_and_negative_prompt_include_directional_details(self) -> None:
        project = self._make_project()
        scene = project.scenes[0]
        prompt = build_cinematic_scene_prompt(project, scene, aspect_ratio="9:16")
        negative = build_scene_negative_prompt("grainy render", scene.negative_prompt)
        self.assertIn("premium generative AI artwork", prompt)
        self.assertIn("dynamic motion energy", prompt)
        self.assertIn("grainy render", negative)
        self.assertIn("text overlay", negative)

    def test_video_project_round_trip_restores_shots(self) -> None:
        project = self._make_project()
        payload = project.to_dict()
        restored = VideoProject.from_dict(payload)
        self.assertEqual(len(restored.scenes), len(project.scenes))
        self.assertEqual(len(restored.scenes[0].shots), len(project.scenes[0].shots))

    def test_storyboard_fallback_frames_respect_requested_size(self) -> None:
        service = StoryboardVideoService()
        project = self._make_project()
        with tempfile.TemporaryDirectory() as temp_dir:
            image_paths = service.render_storyboards(project, temp_dir, size=(720, 1280))
            self.assertEqual(len(image_paths), 3)
            with Image.open(image_paths[0]) as frame:
                self.assertEqual(frame.size, (720, 1280))

    def test_build_video_routes_legacy_calls_through_narrated_storyboard_render(self) -> None:
        service = StoryboardVideoService()
        project = self._make_project()
        captured: dict[str, object] = {}
        expected_output = Path("D:/tmp/legacy-storyboard.mp4")
        fake_frames = [Path("D:/tmp/frame01.png"), Path("D:/tmp/frame02.png"), Path("D:/tmp/frame03.png")]

        def fake_render(request, progress_callback=None, image_paths=None):  # type: ignore[no-untyped-def]
            captured["request"] = request
            captured["image_paths"] = image_paths
            return RenderedVideoResult(provider="Storyboard local", file_path=expected_output)

        service.render = fake_render  # type: ignore[method-assign]

        output = service.build_video(
            project,
            "D:/tmp/out",
            image_paths=fake_frames,
            ffmpeg_path="C:/ffmpeg.exe",
        )

        self.assertEqual(output, expected_output)
        request = captured["request"]
        assert hasattr(request, "provider")
        self.assertEqual(request.provider, "Storyboard local")
        self.assertEqual(request.aspect_ratio, "9:16")
        self.assertTrue(request.render_captions)
        self.assertEqual(request.tts_backend, "Windows local")
        self.assertEqual(request.ffmpeg_path, "C:/ffmpeg.exe")
        self.assertEqual(captured["image_paths"], fake_frames)

    def test_wait_for_completion_raises_immediately_on_execution_error(self) -> None:
        client = ComfyUIClient(base_url="http://127.0.0.1:8188")
        client._get = lambda path: {  # type: ignore[method-assign]
            "prompt-1": {
                "outputs": {},
                "status": {
                    "messages": [
                        [
                            "execution_error",
                            {
                                "node_type": "Echo_LoadModel",
                                "exception_message": "Please install accelerate via `pip install accelerate`",
                            },
                        ]
                    ]
                },
            }
        }
        with self.assertRaisesRegex(RuntimeError, "Echo_LoadModel"):
            client.wait_for_completion("prompt-1", poll_interval_seconds=1, max_wait_seconds=3)

    def test_resolve_comfyui_base_url_checks_desktop_port_first(self) -> None:
        manager = SetupManager()
        checked: list[str] = []

        def fake_reachable(url: str) -> bool:
            checked.append(url)
            return url == "http://127.0.0.1:8000"

        manager._comfyui_reachable = fake_reachable  # type: ignore[method-assign]
        resolved = manager.resolve_comfyui_base_url("http://127.0.0.1:8188")
        self.assertEqual(resolved, "http://127.0.0.1:8000")
        self.assertIn("http://127.0.0.1:8000", checked)

    def test_resolve_comfyui_worker_urls_discovers_multiple_unique_workers(self) -> None:
        manager = SetupManager()

        def fake_reachable(url: str) -> bool:
            return url in {
                "http://127.0.0.1:8000",
                "http://127.0.0.1:8189",
            }

        manager._comfyui_reachable = fake_reachable  # type: ignore[method-assign]
        resolved = manager.resolve_comfyui_worker_urls(
            "http://127.0.0.1:8189, http://127.0.0.1:8000",
            "http://127.0.0.1:8000",
        )
        self.assertEqual(resolved, ["http://127.0.0.1:8189", "http://127.0.0.1:8000"])

    def test_ensure_extra_models_config_writes_managed_section(self) -> None:
        previous_appdata = os.environ.get("APPDATA")
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                os.environ["APPDATA"] = temp_dir
                manager = SetupManager()
                config_path = manager.ensure_extra_models_config()
                content = config_path.read_text(encoding="utf-8")
                self.assertIn("VideoGeniusAI desktop paths begin", content)
                self.assertIn("custom_nodes: custom_nodes", content)
                self.assertIn("VideoGeniusAI managed models begin", content)
                self.assertIn("checkpoints: checkpoints", content)
        finally:
            if previous_appdata is None:
                os.environ.pop("APPDATA", None)
            else:
                os.environ["APPDATA"] = previous_appdata

    def test_wait_for_lmstudio_returns_models_when_ready(self) -> None:
        manager = SetupManager()
        with mock.patch("videogenius_ai.setup_manager.LMStudioClient.test_connection", return_value=(True, ["model-a"], "ok")):
            success, models, message = manager.wait_for_lmstudio("http://127.0.0.1:1234", timeout_seconds=1)
        self.assertTrue(success)
        self.assertEqual(models, ["model-a"])
        self.assertEqual(message, "ok")

    def test_wait_for_comfyui_returns_resolved_url_and_checkpoints(self) -> None:
        manager = SetupManager()
        manager.resolve_comfyui_base_url = lambda configured_url: "http://127.0.0.1:8000"  # type: ignore[method-assign]
        manager._comfyui_reachable = lambda base_url: base_url == "http://127.0.0.1:8000"  # type: ignore[method-assign]
        manager._load_checkpoints = lambda base_url: ["model-a.safetensors"]  # type: ignore[method-assign]
        success, resolved_url, checkpoints, message = manager.wait_for_comfyui(
            "http://127.0.0.1:8188",
            timeout_seconds=1,
            require_checkpoints=True,
        )
        self.assertTrue(success)
        self.assertEqual(resolved_url, "http://127.0.0.1:8000")
        self.assertEqual(checkpoints, ["model-a.safetensors"])
        self.assertIn("Connected successfully", message)


if __name__ == "__main__":
    unittest.main()
