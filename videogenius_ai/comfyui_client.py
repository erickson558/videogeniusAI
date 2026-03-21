from __future__ import annotations

import copy
import json
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from .models import GeneratedSceneAsset
from .utils import sanitize_filename

VIDEO_OUTPUT_CLASS_TYPES = {
    "SaveAnimatedWEBP",
    "VHS_VideoCombine",
    "VideoCombine",
    "SaveVideo",
    "SaveWEBM",
    "SaveGif",
}
IMAGE_OUTPUT_CLASS_TYPES = {
    "SaveImage",
    "PreviewImage",
}


def detect_workflow_output_mode(workflow_path: str | Path) -> str:
    path = Path(workflow_path)
    if not path.exists():
        raise FileNotFoundError(f"ComfyUI workflow file not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("The ComfyUI workflow must be a JSON object exported for API usage.")

    class_types = {
        str(node.get("class_type", "")).strip()
        for node in payload.values()
        if isinstance(node, dict)
    }
    if class_types & VIDEO_OUTPUT_CLASS_TYPES:
        return "video"
    if class_types & IMAGE_OUTPUT_CLASS_TYPES:
        return "image"
    return "unknown"


def _replace_placeholders(value: Any, replacements: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {key: _replace_placeholders(item, replacements) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_placeholders(item, replacements) for item in value]
    if isinstance(value, str):
        for key, replacement in replacements.items():
            if value == key:
                return replacement
        result = value
        for key, replacement in replacements.items():
            result = result.replace(key, str(replacement))
        return result
    return value


@dataclass
class ComfyUIClient:
    base_url: str
    timeout_seconds: int = 180

    def _normalize_base_url(self) -> str:
        base = self.base_url.strip().rstrip("/")
        if not base:
            raise ValueError("ComfyUI base URL cannot be empty.")
        if not base.startswith(("http://", "https://")):
            raise ValueError("ComfyUI base URL must start with http:// or https://")
        return base

    def _get(self, path: str, **kwargs: Any) -> dict[str, Any]:
        response = requests.get(f"{self._normalize_base_url()}{path}", timeout=self.timeout_seconds, **kwargs)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("ComfyUI returned a non-object JSON payload.")
        return payload

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = requests.post(
            f"{self._normalize_base_url()}{path}",
            json=payload,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict):
            raise ValueError("ComfyUI returned a non-object JSON payload.")
        return body

    def test_connection(self) -> tuple[bool, str]:
        try:
            self._get("/history")
        except requests.RequestException as exc:
            return False, str(exc)
        return True, "Connected successfully to ComfyUI."

    def _extract_checkpoint_names(self, payload: dict[str, Any]) -> list[str]:
        if "CheckpointLoaderSimple" in payload and isinstance(payload["CheckpointLoaderSimple"], dict):
            return self._extract_checkpoint_names(payload["CheckpointLoaderSimple"])

        input_section = payload.get("input")
        if isinstance(input_section, dict):
            required = input_section.get("required")
            if isinstance(required, dict):
                ckpt_name = required.get("ckpt_name")
                if isinstance(ckpt_name, list) and ckpt_name:
                    first = ckpt_name[0]
                    if isinstance(first, list):
                        return [str(item) for item in first if str(item).strip()]
                    return [str(item) for item in ckpt_name if str(item).strip()]

        for value in payload.values():
            if isinstance(value, dict):
                candidates = self._extract_checkpoint_names(value)
                if candidates:
                    return candidates
        return []

    def list_checkpoints(self) -> list[str]:
        candidate_paths = [
            "/object_info/CheckpointLoaderSimple",
            "/api/object_info/CheckpointLoaderSimple",
            "/object_info",
            "/api/object_info",
        ]
        for path in candidate_paths:
            try:
                payload = self._get(path)
            except requests.RequestException:
                continue
            checkpoints = self._extract_checkpoint_names(payload)
            if checkpoints:
                return checkpoints
        return []

    def list_node_types(self) -> list[str]:
        for path in ("/object_info", "/api/object_info"):
            try:
                payload = self._get(path)
            except requests.RequestException:
                continue
            return sorted(str(key).strip() for key in payload.keys() if str(key).strip())
        return []

    def has_nodes(self, required_node_types: list[str]) -> tuple[bool, list[str]]:
        available = set(self.list_node_types())
        missing = [node_type for node_type in required_node_types if node_type not in available]
        return not missing, missing

    def _load_workflow(self, workflow_path: str | Path) -> dict[str, Any]:
        path = Path(workflow_path)
        if not path.exists():
            raise FileNotFoundError(f"ComfyUI workflow file not found: {path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("The ComfyUI workflow must be a JSON object exported for API usage.")
        return payload

    def _prepare_workflow(
        self,
        workflow_path: str | Path,
        *,
        prompt_text: str,
        negative_prompt: str,
        output_prefix: str,
        extra_replacements: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        workflow = copy.deepcopy(self._load_workflow(workflow_path))
        seed_value = random.randint(1, 2_147_483_647)
        replacements = {
            "__PROMPT__": prompt_text,
            "__NEGATIVE_PROMPT__": negative_prompt,
            "__SEED__": seed_value,
            "__OUTPUT_PREFIX__": sanitize_filename(output_prefix),
        }
        if extra_replacements:
            replacements.update(extra_replacements)
        return _replace_placeholders(workflow, replacements)

    def queue_prompt(
        self,
        workflow_path: str | Path,
        *,
        prompt_text: str,
        negative_prompt: str,
        output_prefix: str,
        extra_replacements: dict[str, Any] | None = None,
    ) -> str:
        workflow = self._prepare_workflow(
            workflow_path,
            prompt_text=prompt_text,
            negative_prompt=negative_prompt,
            output_prefix=output_prefix,
            extra_replacements=extra_replacements,
        )
        response = self._post("/prompt", {"prompt": workflow})
        prompt_id = response.get("prompt_id")
        if not isinstance(prompt_id, str) or not prompt_id.strip():
            raise ValueError("ComfyUI did not return a prompt_id.")
        return prompt_id.strip()

    def _extract_history_record(self, payload: dict[str, Any], prompt_id: str) -> dict[str, Any]:
        if prompt_id in payload and isinstance(payload[prompt_id], dict):
            return payload[prompt_id]
        return payload

    def _extract_execution_error(self, record: dict[str, Any]) -> str:
        status = record.get("status")
        if not isinstance(status, dict):
            return ""
        messages = status.get("messages")
        if not isinstance(messages, list):
            return ""
        for entry in messages:
            if not isinstance(entry, list) or len(entry) < 2:
                continue
            event_type, details = entry[0], entry[1]
            if event_type != "execution_error" or not isinstance(details, dict):
                continue
            message = str(details.get("exception_message", "")).strip()
            node_type = str(details.get("node_type", "")).strip()
            if message and node_type:
                return f"ComfyUI execution failed in node '{node_type}': {message}"
            if message:
                return f"ComfyUI execution failed: {message}"
        return ""

    def wait_for_completion(
        self,
        prompt_id: str,
        *,
        poll_interval_seconds: int = 2,
        max_wait_seconds: int = 1800,
    ) -> dict[str, Any]:
        started = time.monotonic()
        interval = max(1, int(poll_interval_seconds))

        while True:
            payload = self._get(f"/history/{prompt_id}")
            record = self._extract_history_record(payload, prompt_id)
            error_message = self._extract_execution_error(record)
            if error_message:
                raise RuntimeError(error_message)
            outputs = record.get("outputs")
            if isinstance(outputs, dict) and outputs:
                return record
            if time.monotonic() - started > max_wait_seconds:
                raise TimeoutError("Timed out while waiting for ComfyUI to finish the workflow.")
            time.sleep(interval)

    def _extract_asset_reference(self, record: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        outputs = record.get("outputs")
        if not isinstance(outputs, dict):
            raise ValueError("ComfyUI finished without output assets.")

        for node_output in outputs.values():
            if not isinstance(node_output, dict):
                continue
            for key in ("videos", "gifs", "images"):
                items = node_output.get(key)
                if isinstance(items, list) and items:
                    first = items[0]
                    if isinstance(first, dict):
                        asset_type = "video" if key in {"videos", "gifs"} else "image"
                        return asset_type, first
        raise ValueError("ComfyUI finished, but no image or video outputs were found.")

    def download_asset(self, asset_ref: dict[str, Any], destination_path: str | Path) -> Path:
        filename = asset_ref.get("filename")
        subfolder = asset_ref.get("subfolder", "")
        asset_kind = asset_ref.get("type", "output")
        if not isinstance(filename, str) or not filename.strip():
            raise ValueError("ComfyUI returned an invalid asset reference.")

        params = {
            "filename": filename,
            "subfolder": subfolder,
            "type": asset_kind,
        }
        response = requests.get(f"{self._normalize_base_url()}/view", params=params, timeout=self.timeout_seconds)
        response.raise_for_status()
        destination = Path(destination_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(response.content)
        return destination

    def generate_scene_asset(
        self,
        *,
        workflow_path: str | Path,
        prompt_text: str,
        negative_prompt: str,
        output_prefix: str,
        destination_stem: str | Path,
        poll_interval_seconds: int = 2,
        extra_replacements: dict[str, Any] | None = None,
    ) -> GeneratedSceneAsset:
        prompt_id = self.queue_prompt(
            workflow_path,
            prompt_text=prompt_text,
            negative_prompt=negative_prompt,
            output_prefix=output_prefix,
            extra_replacements=extra_replacements,
        )
        record = self.wait_for_completion(prompt_id, poll_interval_seconds=poll_interval_seconds)
        asset_type, asset_ref = self._extract_asset_reference(record)
        suffix = Path(str(asset_ref.get("filename", ""))).suffix or (".mp4" if asset_type == "video" else ".png")
        destination_path = Path(destination_stem).with_suffix(suffix)
        file_path = self.download_asset(asset_ref, destination_path)
        return GeneratedSceneAsset(asset_type=asset_type, file_path=file_path, source_payload={"prompt_id": prompt_id, "record": record})
