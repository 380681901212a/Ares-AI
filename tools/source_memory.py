"""Lightweight source reputation memory for successful research runs."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlparse

from tools.runtime_paths import WORKSPACE_ROOT


SOURCE_MEMORY_PATH = WORKSPACE_ROOT / "source_memory.json"
_URL_RE = re.compile(r"https?://[^\s)>\]\"']+", re.IGNORECASE)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_memory() -> dict[str, Any]:
    return {"version": 1, "updated_at": "", "sources": {}}


def load_source_memory(path: str | Path | None = None) -> dict[str, Any]:
    memory_path = Path(path or SOURCE_MEMORY_PATH)
    if not memory_path.exists():
        return _default_memory()
    try:
        raw = json.loads(memory_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _default_memory()
    if not isinstance(raw, dict):
        return _default_memory()
    raw.setdefault("version", 1)
    raw.setdefault("updated_at", "")
    raw.setdefault("sources", {})
    return raw


def save_source_memory(memory: dict[str, Any], path: str | Path | None = None) -> dict[str, Any]:
    memory_path = Path(path or SOURCE_MEMORY_PATH)
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    memory["updated_at"] = _now()
    memory_path.write_text(json.dumps(memory, ensure_ascii=False, indent=2), encoding="utf-8")
    return memory


def extract_domains_from_text(text: str) -> list[str]:
    domains: list[str] = []
    seen: set[str] = set()
    for url in _URL_RE.findall(text or ""):
        parsed = urlparse(url.rstrip(".,;"))
        domain = parsed.netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]
        if not domain or domain in seen:
            continue
        seen.add(domain)
        domains.append(domain)
    return domains


def reward_sources_from_text(
    text: str,
    intents: list[str],
    locale: str = "",
    path: str | Path | None = None,
) -> dict[str, Any]:
    domains = extract_domains_from_text(text)
    if not domains:
        return load_source_memory(path)

    memory = load_source_memory(path)
    sources = memory.setdefault("sources", {})
    for domain in domains:
        record = sources.setdefault(
            domain,
            {
                "success_count": 0,
                "intents": {},
                "locales": {},
                "last_success_at": "",
            },
        )
        record["success_count"] = int(record.get("success_count", 0)) + 1
        record["last_success_at"] = _now()
        for intent in intents:
            intent_counts = record.setdefault("intents", {})
            intent_counts[intent] = int(intent_counts.get(intent, 0)) + 1
        if locale:
            locale_counts = record.setdefault("locales", {})
            locale_counts[locale] = int(locale_counts.get(locale, 0)) + 1

    return save_source_memory(memory, path)


def get_preferred_source_domains(
    intents: list[str],
    locale: str = "",
    limit: int = 5,
    path: str | Path | None = None,
) -> list[str]:
    memory = load_source_memory(path)
    scored: list[tuple[int, str]] = []
    for domain, record in memory.get("sources", {}).items():
        score = int(record.get("success_count", 0))
        intent_counts = record.get("intents", {})
        locale_counts = record.get("locales", {})
        score += sum(int(intent_counts.get(intent, 0)) * 3 for intent in intents)
        if locale:
            score += int(locale_counts.get(locale, 0)) * 2
        if score > 0:
            scored.append((score, domain))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [domain for _, domain in scored[:limit]]
