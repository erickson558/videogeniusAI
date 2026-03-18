from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parent
VERSION_FILE = ROOT / "videogenius_ai" / "version.py"
CONFIG_FILE = ROOT / "config.json"
CHANGELOG_FILE = ROOT / "CHANGELOG.md"


def read_version() -> str:
    content = VERSION_FILE.read_text(encoding="utf-8")
    match = re.search(r'APP_VERSION = "(\d+\.\d+\.\d+)"', content)
    if not match:
        raise ValueError("Unable to find APP_VERSION.")
    return match.group(1)


def bump_patch(version: str) -> str:
    major, minor, patch = [int(part) for part in version.split(".")]
    patch += 1
    return f"{major}.{minor}.{patch}"


def write_version(version: str) -> None:
    content = VERSION_FILE.read_text(encoding="utf-8")
    content = re.sub(r'APP_VERSION = "\d+\.\d+\.\d+"', f'APP_VERSION = "{version}"', content)
    VERSION_FILE.write_text(content, encoding="utf-8")

    config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    config["app_version"] = f"V{version}"
    CONFIG_FILE.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")

    changelog = CHANGELOG_FILE.read_text(encoding="utf-8")
    entry = f"## V{version} - {date.today().isoformat()}\n\n- Patch release.\n\n"
    CHANGELOG_FILE.write_text("# Changelog\n\n" + entry + changelog.replace("# Changelog\n\n", "", 1), encoding="utf-8")


if __name__ == "__main__":
    current = read_version()
    new_version = bump_patch(current)
    write_version(new_version)
    print(f"Bumped version: V{current} -> V{new_version}")

