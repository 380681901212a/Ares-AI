"""Helpers for interacting with the local Ollama runtime."""

from __future__ import annotations

import os
from typing import Iterable

import requests

_OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
_CODER_MODEL = os.getenv("ARES_CODER_MODEL", "qwen2.5-coder:14b")
_VISION_MODEL = os.getenv("ARES_VISION_MODEL", "qwen2.5vl:7b")


def get_ollama_base_url() -> str:
    return _OLLAMA_BASE_URL


def get_coder_model_name() -> str:
    return _CODER_MODEL


def get_vision_model_name() -> str:
    return _VISION_MODEL


def _get_json(path: str) -> dict:
    response = requests.get(f"{_OLLAMA_BASE_URL}{path}", timeout=5)
    response.raise_for_status()
    return response.json()


def _post_json(path: str, payload: dict) -> dict:
    response = requests.post(f"{_OLLAMA_BASE_URL}{path}", json=payload, timeout=60)
    response.raise_for_status()
    return response.json()


def list_installed_models() -> list[str]:
    try:
        data = _get_json("/api/tags")
    except Exception:
        return []
    return [model.get("name", "") for model in data.get("models", []) if model.get("name")]


def is_model_installed(model_name: str) -> bool:
    return model_name in set(list_installed_models())

def is_model_available(model_name: str) -> bool:
    return is_model_installed(model_name)

def get_text_model_name() -> str:
    """Prioritizes general-purpose models for text tasks."""
    for model in ["qwen2.5:latest", "qwen2.5:7b", "llama3.1:8b"]:
        if is_model_available(model):
            return model
    return get_coder_model_name()  # fallback


def list_running_models() -> list[str]:
    try:
        data = _get_json("/api/ps")
    except Exception:
        return []
    return [model.get("name", "") for model in data.get("models", []) if model.get("name")]


def unload_model(model_name: str) -> bool:
    if not model_name:
        return False
    try:
        _post_json(
            "/api/generate",
            {
                "model": model_name,
                "prompt": "",
                "stream": False,
                "keep_alive": 0,
            },
        )
        return True
    except Exception:
        return False


def unload_running_models(exclude: Iterable[str] | None = None) -> list[str]:
    excluded = set(exclude or [])
    unloaded = []
    for model_name in list_running_models():
        if model_name in excluded:
            continue
        if unload_model(model_name):
            unloaded.append(model_name)
    return unloaded
