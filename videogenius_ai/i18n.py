from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from typing import Any


DEFAULT_UI_LANGUAGE = "es"
SUPPORTED_UI_LANGUAGES = {
    "es": "Espanol",
    "en": "English",
}


def normalize_ui_language(value: str | None) -> str:
    text = (value or "").strip().lower()
    if text.startswith("en"):
        return "en"
    return DEFAULT_UI_LANGUAGE


def ui_language_label(language: str) -> str:
    return SUPPORTED_UI_LANGUAGES.get(normalize_ui_language(language), SUPPORTED_UI_LANGUAGES[DEFAULT_UI_LANGUAGE])


def ui_language_code_from_label(label: str | None) -> str:
    normalized = (label or "").strip().casefold()
    for code, value in SUPPORTED_UI_LANGUAGES.items():
        if normalized == value.casefold():
            return code
    return normalize_ui_language(label)


def _lookup(payload: dict[str, Any], key: str) -> Any:
    current: Any = payload
    for part in key.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


@lru_cache(maxsize=None)
def _load_catalog(language: str) -> dict[str, Any]:
    resolved = normalize_ui_language(language)
    resource = resources.files("videogenius_ai").joinpath("locales", f"{resolved}.json")
    with resource.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"Locale payload must be a dictionary: {resolved}")
    return payload


class TranslationManager:
    def __init__(self, language: str = DEFAULT_UI_LANGUAGE, fallback_language: str = DEFAULT_UI_LANGUAGE) -> None:
        self.fallback_language = normalize_ui_language(fallback_language)
        self.language = DEFAULT_UI_LANGUAGE
        self.catalog: dict[str, Any] = {}
        self.fallback_catalog: dict[str, Any] = _load_catalog(self.fallback_language)
        self.set_language(language)

    def set_language(self, language: str) -> None:
        self.language = normalize_ui_language(language)
        self.catalog = _load_catalog(self.language)

    def translate(self, key: str, **kwargs: Any) -> str:
        raw_value = _lookup(self.catalog, key)
        if raw_value is None:
            raw_value = _lookup(self.fallback_catalog, key)
        if raw_value is None:
            return key
        if not isinstance(raw_value, str):
            return str(raw_value)
        if not kwargs:
            return raw_value
        try:
            return raw_value.format(**kwargs)
        except (IndexError, KeyError, ValueError):
            return raw_value
