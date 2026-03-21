from __future__ import annotations

import argparse
import re
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parent
VERSION_FILE = ROOT / "videogenius_ai" / "version.py"
CHANGELOG_FILE = ROOT / "CHANGELOG.md"
MANUAL_FILE = ROOT / "MANUAL_USUARIO.md"
README_FILE = ROOT / "README.md"
VERSION_PATTERN = re.compile(r'APP_VERSION = "(\d+\.\d+\.\d+)"')
DISPLAY_VERSION_PATTERN = re.compile(r"V\d+\.\d+\.\d+")


def read_version() -> str:
    content = VERSION_FILE.read_text(encoding="utf-8")
    match = VERSION_PATTERN.search(content)
    if not match:
        raise ValueError("Unable to find APP_VERSION.")
    return match.group(1)


def bump_version(version: str, part: str) -> str:
    major, minor, patch = [int(part) for part in version.split(".")]
    if part == "major":
        major += 1
        minor = 0
        patch = 0
    elif part == "minor":
        minor += 1
        patch = 0
    else:
        patch += 1
    return f"{major}.{minor}.{patch}"


def _display_version(version: str) -> str:
    return f"V{version}"


def update_version_file(version: str) -> None:
    content = VERSION_FILE.read_text(encoding="utf-8")
    content = VERSION_PATTERN.sub(f'APP_VERSION = "{version}"', content)
    VERSION_FILE.write_text(content, encoding="utf-8")


def update_manual(version: str) -> None:
    if not MANUAL_FILE.exists():
        return
    display_version = _display_version(version)
    content = MANUAL_FILE.read_text(encoding="utf-8")
    content = re.sub(
        r"Version actual:\s*`V\d+\.\d+\.\d+`",
        f"Version actual: `{display_version}`",
        content,
        count=1,
    )
    MANUAL_FILE.write_text(content, encoding="utf-8")


def update_readme(version: str) -> None:
    if not README_FILE.exists():
        return
    display_version = _display_version(version)
    content = README_FILE.read_text(encoding="utf-8")
    content = re.sub(
        r"Current app version:\s*`V\d+\.\d+\.\d+`",
        f"Current app version: `{display_version}`",
        content,
        count=1,
    )
    README_FILE.write_text(content, encoding="utf-8")


def update_changelog(version: str, notes: list[str]) -> None:
    changelog = CHANGELOG_FILE.read_text(encoding="utf-8")
    bullet_lines = "\n".join(f"- {note}" for note in notes) if notes else "- Maintenance release."
    entry = f"## {_display_version(version)} - {date.today().isoformat()}\n\n{bullet_lines}\n\n"
    CHANGELOG_FILE.write_text("# Changelog\n\n" + entry + changelog.replace("# Changelog\n\n", "", 1), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bump the VideoGeniusAI version using semantic versioning.")
    parser.add_argument(
        "part",
        nargs="?",
        choices=("patch", "minor", "major"),
        default="patch",
        help="Version segment to increment. Defaults to patch.",
    )
    parser.add_argument(
        "--note",
        dest="notes",
        action="append",
        default=[],
        help="Release note bullet to prepend to the changelog entry. Can be passed multiple times.",
    )
    return parser.parse_args()


def write_version(version: str, notes: list[str]) -> None:
    update_version_file(version)
    update_readme(version)
    update_manual(version)
    update_changelog(version, notes)


if __name__ == "__main__":
    args = parse_args()
    current = read_version()
    new_version = bump_version(current, args.part)
    write_version(new_version, args.notes)
    print(f"Bumped version: {_display_version(current)} -> {_display_version(new_version)}")
