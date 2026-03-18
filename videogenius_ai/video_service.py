from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path
from shutil import which

from PIL import Image, ImageDraw, ImageFont

from .models import VideoProject
from .utils import now_stamp, sanitize_filename

CREATE_NO_WINDOW = 0x08000000


def _pick_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        Path("C:/Windows/Fonts/segoeuib.ttf"),
        Path("C:/Windows/Fonts/segoeui.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


class StoryboardVideoService:
    def render_storyboards(self, project: VideoProject, output_dir: str | Path) -> list[Path]:
        target_dir = Path(output_dir)
        storyboard_dir = target_dir / f"{now_stamp()}_{sanitize_filename(project.title)}_storyboard"
        storyboard_dir.mkdir(parents=True, exist_ok=True)

        title_font = _pick_font(54)
        body_font = _pick_font(30)
        small_font = _pick_font(24)
        image_paths: list[Path] = []

        for scene in project.scenes:
            image = Image.new("RGB", (1280, 720), "#0F172A")
            draw = ImageDraw.Draw(image)

            for y in range(720):
                red = int(15 + (y / 720) * 60)
                green = int(23 + (y / 720) * 90)
                blue = int(42 + (y / 720) * 70)
                draw.line([(0, y), (1280, y)], fill=(red, green, blue))

            draw.rounded_rectangle((60, 60, 1220, 660), radius=36, fill="#F8FAFC", outline="#F97316", width=4)
            draw.rounded_rectangle((90, 90, 1190, 190), radius=24, fill="#111827")
            draw.text((120, 115), f"Scene {scene.scene_number}: {scene.scene_title}", font=title_font, fill="#F8FAFC")

            prompt_text = textwrap.fill(scene.visual_prompt or scene.description or "Storyboard frame", width=46)
            narration_text = textwrap.fill(scene.narration or "No narration generated.", width=52)

            draw.text((120, 230), "Visual prompt", font=body_font, fill="#0F172A")
            draw.text((120, 280), prompt_text, font=small_font, fill="#1F2937", spacing=8)

            draw.text((120, 455), "Narration", font=body_font, fill="#0F172A")
            draw.text((120, 505), narration_text, font=small_font, fill="#374151", spacing=8)

            draw.rounded_rectangle((930, 560, 1150, 625), radius=18, fill="#14B8A6")
            draw.text((960, 579), f"{scene.duration_seconds}s", font=small_font, fill="#F8FAFC")

            file_path = storyboard_dir / f"scene_{scene.scene_number:02d}.png"
            image.save(file_path, format="PNG")
            image_paths.append(file_path)

        return image_paths

    def _write_concat_manifest(self, image_paths: list[Path], durations: list[int], manifest_path: Path) -> None:
        lines = []
        for image_path, duration in zip(image_paths, durations):
            lines.append(f"file '{image_path.as_posix()}'")
            lines.append(f"duration {duration}")
        if image_paths:
            lines.append(f"file '{image_paths[-1].as_posix()}'")
        manifest_path.write_text("\n".join(lines), encoding="utf-8")

    def build_video(
        self,
        project: VideoProject,
        output_dir: str | Path,
        image_paths: list[Path] | None = None,
        ffmpeg_path: str = "",
    ) -> Path:
        ffmpeg_path = ffmpeg_path.strip() if ffmpeg_path.strip() and Path(ffmpeg_path).exists() else (which("ffmpeg") or which("ffmpeg.exe"))
        if not ffmpeg_path:
            raise FileNotFoundError("FFmpeg is not available in PATH.")

        target_dir = Path(output_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        image_paths = image_paths or self.render_storyboards(project, target_dir)
        manifest_path = target_dir / f"{sanitize_filename(project.title)}_manifest.txt"
        output_path = target_dir / f"{now_stamp()}_{sanitize_filename(project.title)}.mp4"

        self._write_concat_manifest(
            image_paths=image_paths,
            durations=[scene.duration_seconds for scene in project.scenes],
            manifest_path=manifest_path,
        )

        command = [
            ffmpeg_path,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(manifest_path),
            "-f",
            "lavfi",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-shortest",
            "-r",
            "30",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(output_path),
        ]

        subprocess.run(
            command,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=CREATE_NO_WINDOW,
        )
        return output_path
