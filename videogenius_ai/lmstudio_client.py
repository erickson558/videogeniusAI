from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests

from .utils import strip_reasoning_sections


def is_embedding_model(model_id: str) -> bool:
    lowered = model_id.strip().lower()
    return "embedding" in lowered or "embed" in lowered


def is_reasoning_model(model_id: str) -> bool:
    lowered = model_id.strip().lower()
    markers = (
        "deepseek-r1",
        "/r1",
        "-r1",
        "_r1",
        "reasoning",
        "reasoner",
        "qwq",
        "thinking",
    )
    return any(marker in lowered for marker in markers)


def sort_models_for_generation(models: list[str]) -> list[str]:
    unique_models = list(dict.fromkeys(model for model in models if model))

    def sort_key(model_id: str) -> tuple[int, int, int, str]:
        lowered = model_id.lower()
        has_chat_hint = any(token in lowered for token in ("chat", "instruct", "assistant"))
        is_code_model = any(token in lowered for token in ("code", "codellama", "copilot"))
        family_hint = any(token in lowered for token in ("gpt", "gemma", "llama", "qwen", "mistral", "phi"))
        return (
            1 if is_embedding_model(model_id) else 0,
            1 if is_reasoning_model(model_id) else 0,
            0 if has_chat_hint else 1,
            1 if is_code_model else 0,
            0 if family_hint else 1,
            lowered,
        )

    return sorted(unique_models, key=sort_key)


@dataclass
class LMStudioClient:
    base_url: str
    api_key: str = ""
    timeout_seconds: int = 120

    def _normalize_base_url(self) -> str:
        base = self.base_url.strip().rstrip("/")
        if not base:
            raise ValueError("LM Studio base URL cannot be empty.")
        if not base.startswith(("http://", "https://")):
            raise ValueError("LM Studio base URL must start with http:// or https://")
        if not base.endswith("/v1"):
            base = f"{base}/v1"
        return base

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key.strip():
            headers["Authorization"] = f"Bearer {self.api_key.strip()}"
        return headers

    def _get(self, path: str) -> dict[str, Any]:
        response = requests.get(
            f"{self._normalize_base_url()}{path}",
            headers=self._headers(),
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        return response.json()

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = requests.post(
            f"{self._normalize_base_url()}{path}",
            headers=self._headers(),
            json=payload,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        return response.json()

    def list_models(self) -> list[str]:
        response = self._get("/models")
        models = [
            item["id"]
            for item in response.get("data", [])
            if isinstance(item, dict) and item.get("id")
        ]
        return sort_models_for_generation(models)

    def test_connection(self) -> tuple[bool, list[str], str]:
        try:
            models = self.list_models()
        except requests.RequestException as exc:
            return False, [], str(exc)

        if not models:
            return True, [], "Connected, but no models were returned by LM Studio."
        return True, models, f"Connected successfully. {len(models)} model(s) available."

    def _timeout_message(self, model: str) -> str:
        message = (
            f"LM Studio timed out while waiting for model '{model}'. "
            f"The local server at {self._normalize_base_url()} did not finish within {self.timeout_seconds}s. "
        )
        if is_reasoning_model(model):
            message += (
                "This model appears to be a reasoning model and may spend too long in '<think>' output "
                "before returning the JSON that VideoGeniusAI expects. "
            )
        message += "Try a non-reasoning chat/instruct model, reduce max tokens, or confirm the model is fully loaded in LM Studio."
        return message

    def chat_completion(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
        expect_json: bool = True,
    ) -> str:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if expect_json:
            payload["response_format"] = {"type": "json_object"}

        try:
            response = self._post("/chat/completions", payload)
        except requests.ReadTimeout as exc:
            raise requests.ReadTimeout(self._timeout_message(model)) from exc
        except requests.HTTPError as exc:
            response = exc.response
            if response is not None and response.status_code == 400 and expect_json:
                payload.pop("response_format", None)
                try:
                    response = self._post("/chat/completions", payload)
                except requests.ReadTimeout as timeout_exc:
                    raise requests.ReadTimeout(self._timeout_message(model)) from timeout_exc
            else:
                raise

        choices = response.get("choices", [])
        if not choices:
            raise ValueError("LM Studio returned no choices.")

        message = choices[0].get("message", {})
        content = strip_reasoning_sections(str(message.get("content", "")))
        if not content:
            details = "LM Studio returned an empty message after removing reasoning text."
            if is_reasoning_model(model):
                details += " Select a non-reasoning chat/instruct model for strict JSON generation."
            raise ValueError(details)
        if expect_json and "{" not in content and "[" not in content:
            details = "LM Studio returned no JSON content."
            if is_reasoning_model(model):
                details += " The selected model appears to be a reasoning model; prefer a non-reasoning chat/instruct model."
            raise ValueError(details)
        return content
