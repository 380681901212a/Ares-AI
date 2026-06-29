"""Multimodal image analysis via a local Ollama vision model."""

from __future__ import annotations

import base64
import json
import pathlib
from typing import Any

import requests

from tools.ollama_runtime import (
    get_ollama_base_url,
    get_vision_model_name,
    is_model_installed,
    unload_model,
)

_VISION_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "is_relevant": {"type": "boolean"},
        "best_match_query": {"type": "string"},
        "confidence": {"type": "number"},
        "labels": {
            "type": "array",
            "items": {"type": "string"},
        },
        "short_description": {"type": "string"},
        "reason": {"type": "string"},
        "image_kind": {"type": "string"},
        "is_real_photo": {"type": "boolean"},
    },
    "required": [
        "is_relevant",
        "best_match_query",
        "confidence",
        "labels",
        "short_description",
        "reason",
        "image_kind",
        "is_real_photo",
    ],
}


def _extract_json(raw: str) -> dict[str, Any]:
    raw = (raw or "").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(raw[start:end + 1])
            except json.JSONDecodeError:
                return {}
    return {}


def analyze_image_with_vision(
    image_path: str,
    required_queries: list[str],
    unload_after: bool = True,
) -> dict[str, Any]:
    model_name = get_vision_model_name()
    if not is_model_installed(model_name):
        return {
            "ok": False,
            "error": (
                f"Vision model '{model_name}' is not installed in Ollama. "
                "Pull it before enabling multimodal inspection."
            ),
            "model": model_name,
        }

    image_bytes = pathlib.Path(image_path).read_bytes()
    encoded_image = base64.b64encode(image_bytes).decode("utf-8")
    prompt = (
        "You are validating an image for an automation pipeline. "
        "Decide if this image is relevant to ANY of the required photo queries. "
        "Be strict and prefer false if uncertain. "
        "If it matches, choose the single best query from the provided list.\n\n"
        f"Required queries: {json.dumps(required_queries, ensure_ascii=False)}\n\n"
        "Return JSON only."
    )


    try:
        response = requests.post(
            f"{get_ollama_base_url()}/api/chat",
            json={
                "model": model_name,
                "messages": [
                    {
                        "role": "user",
                        "content": prompt,
                        "images": [encoded_image],
                    }
                ],
                "stream": False,
                "format": _VISION_RESPONSE_SCHEMA,
                "options": {
                    "temperature": 0,
                    "num_ctx": 4096,
                },
                "keep_alive": 0,
            },
            timeout=180,
        )
        response.raise_for_status()
        payload = response.json()
        content = payload.get("message", {}).get("content", "")
        parsed = _extract_json(content)
        if not parsed:
            return {
                "ok": False,
                "error": "Vision model returned invalid JSON.",
                "model": model_name,
            }
        return {
            "ok": True,
            "model": model_name,
            "decision": parsed,
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "model": model_name,
        }
    finally:
        if unload_after:
            unload_model(model_name)
