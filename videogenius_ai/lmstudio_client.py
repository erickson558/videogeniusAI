from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests


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
        return [
            item["id"]
            for item in response.get("data", [])
            if isinstance(item, dict) and item.get("id")
        ]

    def test_connection(self) -> tuple[bool, list[str], str]:
        try:
            models = self.list_models()
        except requests.RequestException as exc:
            return False, [], str(exc)

        if not models:
            return True, [], "Connected, but no models were returned by LM Studio."
        return True, models, f"Connected successfully. {len(models)} model(s) available."

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
        except requests.HTTPError as exc:
            response = exc.response
            if response is not None and response.status_code == 400 and expect_json:
                payload.pop("response_format", None)
                response = self._post("/chat/completions", payload)
            else:
                raise

        choices = response.get("choices", [])
        if not choices:
            raise ValueError("LM Studio returned no choices.")

        message = choices[0].get("message", {})
        content = message.get("content", "")
        if not content:
            raise ValueError("LM Studio returned an empty message.")
        return content

