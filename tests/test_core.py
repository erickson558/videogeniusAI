from __future__ import annotations

import tempfile
import unittest
import os
from pathlib import Path

from videogenius_ai.comfyui_client import ComfyUIClient, _replace_placeholders
from videogenius_ai.config import ConfigManager, sanitize_window_geometry
from videogenius_ai.export_service import ExportService
from videogenius_ai.generator_service import SceneGeneratorService
from videogenius_ai.setup_manager import SetupManager
from videogenius_ai.models import GenerationRequest
from videogenius_ai.video_render_service import VideoRenderService
from videogenius_ai.utils import parse_json_payload


class JsonParsingTests(unittest.TestCase):
    def test_parse_json_from_markdown_fence(self) -> None:
        raw = """```json\n{"title":"Demo","scenes":[{"scene_number":1}]}\n```"""
        payload = parse_json_payload(raw)
        self.assertEqual(payload["title"], "Demo")


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
                appearance_mode="dark",
                output_dir=str(Path(temp_dir) / "output"),
                video_provider="Local AI video",
                render_captions=False,
                comfyui_base_url="http://127.0.0.1:8188",
            )

            reloaded = ConfigManager(config_path=config_path)
            self.assertEqual(reloaded.config.appearance_mode, "dark")
            self.assertEqual(reloaded.config.video_provider, "Local AI video")
            self.assertFalse(reloaded.config.render_captions)
            self.assertEqual(reloaded.config.comfyui_base_url, "http://127.0.0.1:8188")

    def test_invalid_window_geometry_falls_back_to_default(self) -> None:
        self.assertEqual(sanitize_window_geometry("160x160+50+50"), "1460x900+80+40")
        self.assertEqual(sanitize_window_geometry("bad-value"), "1460x900+80+40")
        self.assertEqual(sanitize_window_geometry("1460x900+80+40"), "1460x900+80+40")


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

    def test_ensure_extra_models_config_writes_managed_section(self) -> None:
        previous_appdata = os.environ.get("APPDATA")
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                os.environ["APPDATA"] = temp_dir
                manager = SetupManager()
                config_path = manager.ensure_extra_models_config()
                content = config_path.read_text(encoding="utf-8")
                self.assertIn("VideoGeniusAI managed models begin", content)
                self.assertIn("checkpoints: checkpoints", content)
        finally:
            if previous_appdata is None:
                os.environ.pop("APPDATA", None)
            else:
                os.environ["APPDATA"] = previous_appdata


if __name__ == "__main__":
    unittest.main()
