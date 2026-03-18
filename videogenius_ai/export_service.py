from __future__ import annotations

import csv
import json
from pathlib import Path

from .models import VideoProject
from .utils import now_stamp, sanitize_filename


class ExportService:
    def build_stem(self, project: VideoProject) -> str:
        return f"{now_stamp()}_{sanitize_filename(project.title)}"

    def export_json(self, project: VideoProject, output_dir: str | Path) -> Path:
        target_dir = Path(output_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        file_path = target_dir / f"{self.build_stem(project)}.json"
        with file_path.open("w", encoding="utf-8") as handle:
            json.dump(project.to_dict(), handle, indent=2, ensure_ascii=False)
        return file_path

    def export_txt(self, project: VideoProject, output_dir: str | Path) -> Path:
        target_dir = Path(output_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        file_path = target_dir / f"{self.build_stem(project)}.txt"
        lines = [
            f"Title: {project.title}",
            f"Summary: {project.summary}",
            f"General script: {project.general_script}",
            f"Structure: {project.structure}",
            f"Language: {project.output_language}",
            f"Mode: {project.generation_mode}",
            "",
        ]

        for scene in project.scenes:
            lines.extend(
                [
                    f"Scene {scene.scene_number}: {scene.scene_title}",
                    f"Description: {scene.description}",
                    f"Visual description: {scene.visual_description}",
                    f"Visual prompt: {scene.visual_prompt}",
                    f"Narration: {scene.narration}",
                    f"Duration: {scene.duration_seconds}s",
                    f"Transition: {scene.transition}",
                    "",
                ]
            )

        with file_path.open("w", encoding="utf-8") as handle:
            handle.write("\n".join(lines))
        return file_path

    def export_csv(self, project: VideoProject, output_dir: str | Path) -> Path:
        target_dir = Path(output_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        file_path = target_dir / f"{self.build_stem(project)}.csv"
        with file_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "scene_number",
                    "scene_title",
                    "description",
                    "visual_description",
                    "visual_prompt",
                    "narration",
                    "duration_seconds",
                    "transition",
                ],
            )
            writer.writeheader()
            for scene in project.scenes:
                writer.writerow(scene.to_dict())
        return file_path

