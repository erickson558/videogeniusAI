from __future__ import annotations

import csv
import json
from pathlib import Path

from .models import VideoProject
from .prompt_director import summarize_scene_shots
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
                    f"Cinematic intent: {scene.cinematic_intent}",
                    f"Camera language: {scene.camera_language}",
                    f"Lighting style: {scene.lighting_style}",
                    f"Color palette: {scene.color_palette}",
                    f"Energy level: {scene.energy_level}",
                    f"Negative prompt: {scene.negative_prompt}",
                    f"Shots: {summarize_scene_shots(scene) or '[auto]'}",
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
                    "cinematic_intent",
                    "camera_language",
                    "lighting_style",
                    "color_palette",
                    "energy_level",
                    "negative_prompt",
                    "shot_count",
                    "shot_summary",
                    "narration",
                    "duration_seconds",
                    "transition",
                ],
            )
            writer.writeheader()
            for scene in project.scenes:
                writer.writerow(
                    {
                        "scene_number": scene.scene_number,
                        "scene_title": scene.scene_title,
                        "description": scene.description,
                        "visual_description": scene.visual_description,
                        "visual_prompt": scene.visual_prompt,
                        "cinematic_intent": scene.cinematic_intent,
                        "camera_language": scene.camera_language,
                        "lighting_style": scene.lighting_style,
                        "color_palette": scene.color_palette,
                        "energy_level": scene.energy_level,
                        "negative_prompt": scene.negative_prompt,
                        "shot_count": len(scene.shots),
                        "shot_summary": summarize_scene_shots(scene),
                        "narration": scene.narration,
                        "duration_seconds": scene.duration_seconds,
                        "transition": scene.transition,
                    }
                )
        return file_path
