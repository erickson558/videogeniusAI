from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from videogenius_ai.export_service import ExportService
from videogenius_ai.generator_service import SceneGeneratorService
from videogenius_ai.models import GenerationRequest
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


if __name__ == "__main__":
    unittest.main()
