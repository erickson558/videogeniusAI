from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .models import VideoProject
from .paths import HISTORY_DIR
from .utils import now_stamp, sanitize_filename


@dataclass
class HistoryEntry:
    title: str
    file_path: Path
    created_at: str


class HistoryService:
    def __init__(self, history_dir: Path | None = None) -> None:
        self.history_dir = history_dir or HISTORY_DIR
        self.history_dir.mkdir(parents=True, exist_ok=True)

    def save(self, project: VideoProject) -> Path:
        file_name = f"{now_stamp()}_{sanitize_filename(project.title)}.json"
        file_path = self.history_dir / file_name
        with file_path.open("w", encoding="utf-8") as handle:
            json.dump(project.to_dict(), handle, indent=2, ensure_ascii=False)
        return file_path

    def list_entries(self, limit: int = 100) -> list[HistoryEntry]:
        entries: list[HistoryEntry] = []
        for file_path in sorted(self.history_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            entries.append(
                HistoryEntry(
                    title=file_path.stem,
                    file_path=file_path,
                    created_at=file_path.stem.split("_", maxsplit=2)[0],
                )
            )
        return entries[:limit]

    def load(self, file_path: str | Path) -> VideoProject:
        with Path(file_path).open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return VideoProject.from_dict(payload)

