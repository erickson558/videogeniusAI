from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def sanitize_filename(value: str, fallback: str = "project") -> str:
    cleaned = re.sub(r"[^\w\s.-]", "", value, flags=re.ASCII).strip()
    cleaned = re.sub(r"[\s]+", "_", cleaned)
    return cleaned[:80] or fallback


def strip_markdown_fences(text: str) -> str:
    body = text.strip()
    if body.startswith("```"):
        lines = body.splitlines()
        if len(lines) >= 3:
            body = "\n".join(lines[1:-1]).strip()
    return body


def strip_reasoning_sections(text: str) -> str:
    body = strip_markdown_fences(text)
    body = re.sub(r"<think>.*?</think>", "", body, flags=re.IGNORECASE | re.DOTALL).strip()

    if "<think>" in body.lower():
        start_positions = [index for index in (body.find("{"), body.find("[")) if index != -1]
        if start_positions:
            body = body[min(start_positions) :].strip()
        else:
            body = body.replace("<think>", "").replace("</think>", "").strip()
    return body


def extract_json_candidate(text: str) -> str:
    body = strip_reasoning_sections(text)

    if body.startswith("{") and body.endswith("}"):
        return body
    if body.startswith("[") and body.endswith("]"):
        return body

    start_positions = [index for index in (body.find("{"), body.find("[")) if index != -1]
    if not start_positions:
        return body

    start = min(start_positions)
    opening = body[start]
    closing = "}" if opening == "{" else "]"
    depth = 0

    for index in range(start, len(body)):
        character = body[index]
        if character == opening:
            depth += 1
        elif character == closing:
            depth -= 1
            if depth == 0:
                return body[start : index + 1]

    return body[start:]


def repair_json_candidate(text: str) -> str:
    candidate = extract_json_candidate(text).replace("\r", "")
    candidate = _escape_control_chars_in_strings(candidate)
    candidate = re.sub(r"//.*?$", "", candidate, flags=re.MULTILINE)
    candidate = re.sub(r"/\*.*?\*/", "", candidate, flags=re.DOTALL)
    candidate = re.sub(r",(\s*[}\]])", r"\1", candidate)
    candidate = re.sub(
        r'([{,]\s*)([A-Za-z_][A-Za-z0-9_\- ]*)(\s*:)',
        lambda match: f'{match.group(1)}"{match.group(2).strip()}"{match.group(3)}',
        candidate,
    )
    return candidate


def normalize_search_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", (text or "").casefold())
    return "".join(character for character in normalized if not unicodedata.combining(character))


def brief_requests_silent_narration(text: str) -> bool:
    normalized = normalize_search_text(text)
    patterns = [
        r"\bsin narracion\b",
        r"\bsin voz\b",
        r"\bno narration\b",
        r"\bwithout narration\b",
        r"\bno voiceover\b",
        r"\bwithout voiceover\b",
        r"\bno voice\b",
        r"\bwithout voice\b",
        r"\bsilent video\b",
        r"\bvideo mudo\b",
        r"\bmute\b",
    ]
    return any(re.search(pattern, normalized) for pattern in patterns)


def _escape_control_chars_in_strings(text: str) -> str:
    escaped: list[str] = []
    in_string = False
    escaping = False
    for character in text:
        if in_string:
            if escaping:
                escaped.append(character)
                escaping = False
                continue
            if character == "\\":
                escaped.append(character)
                escaping = True
                continue
            if character == '"':
                escaped.append(character)
                in_string = False
                continue
            if character == "\n":
                escaped.append("\\n")
                continue
            if character == "\t":
                escaped.append("\\t")
                continue
            if ord(character) < 32:
                escaped.append(f"\\u{ord(character):04x}")
                continue
        else:
            if character == '"':
                in_string = True
        escaped.append(character)
    return "".join(escaped)


def parse_json_payload(text: str) -> dict[str, Any]:
    candidate = extract_json_candidate(text)
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError as exc:
        repaired_candidate = repair_json_candidate(text)
        try:
            payload = json.loads(repaired_candidate)
        except json.JSONDecodeError:
            raise ValueError(f"Unable to parse JSON response: {exc}") from exc

    if not isinstance(payload, dict):
        raise ValueError("The LM Studio response must be a JSON object.")
    return payload


def safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def ensure_directory(path: str | Path) -> Path:
    directory = Path(path)
    directory.mkdir(parents=True, exist_ok=True)
    return directory
