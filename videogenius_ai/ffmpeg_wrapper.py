from __future__ import annotations

import subprocess
from pathlib import Path
from shutil import which

CREATE_NO_WINDOW = 0x08000000


class FFmpegWrapper:
    def __init__(self, ffmpeg_path: str = "") -> None:
        resolved_ffmpeg = ffmpeg_path.strip() if ffmpeg_path.strip() and Path(ffmpeg_path).exists() else (which("ffmpeg") or which("ffmpeg.exe"))
        if not resolved_ffmpeg:
            raise FileNotFoundError("FFmpeg is not available in PATH.")

        sibling = Path(resolved_ffmpeg).with_name("ffprobe.exe")
        resolved_ffprobe = str(sibling.resolve()) if sibling.exists() else ""
        if not resolved_ffprobe:
            resolved_ffprobe = which("ffprobe") or which("ffprobe.exe") or ""
        if not resolved_ffprobe:
            raise FileNotFoundError("FFprobe is not available in PATH.")

        self.ffmpeg_path = str(Path(resolved_ffmpeg).resolve())
        self.ffprobe_path = str(Path(resolved_ffprobe).resolve())

    def run(self, command: list[str], *, text: bool = False) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=CREATE_NO_WINDOW,
            text=text,
        )

    def media_duration(self, file_path: str | Path) -> float:
        result = self.run(
            [
                self.ffprobe_path,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(file_path),
            ],
            text=True,
        )
        return max(0.1, float(result.stdout.strip()))
