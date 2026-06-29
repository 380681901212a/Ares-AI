"""Helpers for tracking run-scoped media assets."""

from __future__ import annotations

from datetime import datetime
import json
import pathlib
import re
from typing import Any


def normalize_query(query: str) -> str:
    return re.sub(r"\s+", " ", (query or "").strip().lower())


def normalize_image_requirement(query: str) -> str:
    normalized = normalize_query(query)
    if not normalized:
        return ""

    keyword_groups = [
        ("front_view", ("front", "nose", "head-on", "front-end", "front end", "grille", "fascia", "headlight")),
        ("rear_view", ("rear", "back", "taillight", "tail", "rear-end", "rear end", "tailgate")),
        ("interior", ("interior", "cabin", "dashboard", "cockpit", "inside")),
        ("logo", ("logo", "badge", "emblem")),
        ("wheel", ("wheel", "rim")),
        ("side_view", ("side", "profile")),
        ("exterior", ("exterior", "outside", "road")),
        ("engine", ("engine", "powertrain")),
    ]

    for label, keywords in keyword_groups:
        if any(keyword in normalized for keyword in keywords):
            return label

    return normalized


def infer_task_subject(task: str) -> str:
    task = re.sub(r"\s+", " ", (task or "").strip())
    if not task:
        return ""

    patterns = [
        r"\b((?:19|20)\d{2}\s+[A-Za-z][A-Za-z0-9-]*\s+[A-Za-z0-9-]+(?:\s+[A-Za-z0-9-]+)?)\b",
        r"\b([A-Za-z][A-Za-z0-9-]*\s+[A-Za-z0-9-]+(?:\s+[A-Za-z0-9-]+)?\s+(?:19|20)\d{2})\b",
        r"\b([A-Za-z][A-Za-z0-9-]*\s+[A-Za-z0-9-]+(?:\s+[A-Za-z0-9-]+)?)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, task)
        if match:
            return re.sub(r"\s+", " ", match.group(1)).strip(" .,")

    trimmed = re.sub(r"[\r\n]+", " ", task).strip(" .,")
    return trimmed[:80].strip()


def extract_task_media_requirements(task: str) -> list[str]:
    normalized = normalize_query(task)
    if not normalized:
        return []

    requirement_checks = [
        ("front_view", ("front", "front view", "front part", "front side", "перед", "передн", "переднь")),
        ("rear_view", ("rear", "rear view", "rear part", "back view", "зад", "задн", "заднь")),
        ("interior", ("interior", "cabin", "dashboard", "cockpit", "салон", "інтер", "интер")),
        ("logo", ("logo", "emblem", "badge", "логотип", "емблем")),
        ("side_view", ("side view", "profile", "side profile", "бок", "профіль")),
    ]

    requirements: list[str] = []
    for requirement_key, keywords in requirement_checks:
        if any(keyword in normalized for keyword in keywords):
            requirements.append(requirement_key)

    if not requirements and any(
        keyword in normalized
        for keyword in ("photo", "photos", "image", "images", "picture", "pictures", "фото", "зображ")
    ):
        requirements.append("exterior")

    return requirements


def build_canonical_image_queries(task: str) -> list[str]:
    requirement_keys = extract_task_media_requirements(task)
    if not requirement_keys:
        return []

    subject = infer_task_subject(task)
    subject_prefix = f"{subject} " if subject else ""
    templates = {
        "front_view": f"{subject_prefix}front view real photo".strip(),
        "rear_view": f"{subject_prefix}rear view real photo".strip(),
        "interior": f"{subject_prefix}interior real photo".strip(),
        "side_view": f"{subject_prefix}side view real photo".strip(),
        "exterior": f"{subject_prefix}exterior real photo".strip(),
    }

    queries: list[str] = []
    for requirement_key in requirement_keys:
        if requirement_key == "logo":
            continue
        query = templates.get(requirement_key, "")
        if query and query not in queries:
            queries.append(query)
    return queries


def _default_manifest(run_id: str = "") -> dict[str, Any]:
    return {
        "run_id": run_id,
        "assets": [],
    }


def load_manifest(manifest_path: str) -> dict[str, Any]:
    path = pathlib.Path(manifest_path)
    if not path.exists():
        return _default_manifest()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _default_manifest()
    if not isinstance(data, dict):
        return _default_manifest()
    data.setdefault("run_id", "")
    data.setdefault("assets", [])
    if not isinstance(data["assets"], list):
        data["assets"] = []
    return data


def save_manifest(manifest_path: str, manifest: dict[str, Any]) -> None:
    path = pathlib.Path(manifest_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def ensure_manifest(manifest_path: str, run_id: str = "") -> dict[str, Any]:
    path = pathlib.Path(manifest_path)
    if not path.exists():
        manifest = _default_manifest(run_id)
        save_manifest(manifest_path, manifest)
        return manifest
    manifest = load_manifest(manifest_path)
    if run_id and not manifest.get("run_id"):
        manifest["run_id"] = run_id
        save_manifest(manifest_path, manifest)
    return manifest


def register_asset(manifest_path: str, asset: dict[str, Any], run_id: str = "") -> dict[str, Any]:
    manifest = ensure_manifest(manifest_path, run_id)
    assets = manifest.setdefault("assets", [])
    asset_path = str(asset.get("path", ""))
    for existing in assets:
        if existing.get("path") == asset_path:
            existing.update(asset)
            save_manifest(manifest_path, manifest)
            return existing

    asset.setdefault("status", "unverified")
    asset.setdefault("reason", "")
    asset.setdefault("inspected_at", "")
    asset.setdefault("downloaded_at", datetime.utcnow().isoformat())
    asset.setdefault("requirement_key", normalize_image_requirement(asset.get("query", "")))
    assets.append(asset)
    save_manifest(manifest_path, manifest)
    return asset


def register_discovered_files(
    manifest_path: str,
    run_workspace_dir: str,
    run_id: str = "",
) -> dict[str, Any]:
    manifest = ensure_manifest(manifest_path, run_id)
    known_paths = {asset.get("path") for asset in manifest.get("assets", [])}
    workspace = pathlib.Path(run_workspace_dir)
    if not workspace.exists():
        return manifest

    for entry in sorted(workspace.iterdir()):
        if not entry.is_file():
            continue
        if entry.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}:
            continue
        full_path = str(entry.resolve())
        if full_path in known_paths:
            continue
        register_asset(
            manifest_path,
            {
                "path": full_path,
                "relative_path": f"workspace/{entry.name}",
                "filename": entry.name,
                "query": "",
                "requirement_key": "",
                "source_url": "",
                "status": "unverified",
                "reason": "",
                "origin": "local_scan",
            },
            run_id=run_id,
        )
        known_paths.add(full_path)
    return load_manifest(manifest_path)


def update_asset(manifest_path: str, asset_path: str, **updates: Any) -> dict[str, Any]:
    manifest = ensure_manifest(manifest_path)
    for asset in manifest.setdefault("assets", []):
        if asset.get("path") == asset_path:
            asset.update(updates)
            save_manifest(manifest_path, manifest)
            return asset
    raise KeyError(f"Asset not found in manifest: {asset_path}")


def summarize_manifest(manifest: dict[str, Any], required_queries: list[str] | None = None) -> dict[str, Any]:
    required_queries = required_queries or []
    required_keys = {normalize_image_requirement(query) for query in required_queries if query.strip()}
    accepted_assets = []
    for asset in manifest.get("assets", []):
        if asset.get("status") != "accepted":
            continue
        if required_keys and asset.get("requirement_key", "") not in required_keys:
            continue
        accepted_assets.append(asset)

    satisfied_keys = {
        asset.get("requirement_key", "")
        for asset in accepted_assets
        if asset.get("requirement_key", "")
    }
    missing_queries = [
        query for query in required_queries
        if normalize_image_requirement(query) not in satisfied_keys
    ]

    return {
        "run_id": manifest.get("run_id", ""),
        "asset_count": len(manifest.get("assets", [])),
        "accepted_count": len(accepted_assets),
        "accepted_paths": [asset.get("path", "") for asset in accepted_assets],
        "missing_queries": missing_queries,
        "accepted_queries": sorted(satisfied_keys),
    }
