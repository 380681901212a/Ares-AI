"""Global media cache used to reuse verified assets across runs."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any

from tools.asset_manifest import normalize_image_requirement, normalize_query, register_asset
from tools.runtime_paths import GLOBAL_ASSET_INDEX_PATH, RUNS_ROOT, WORKSPACE_ROOT

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
_GENERIC_KEYWORDS = {
    "a",
    "an",
    "and",
    "angle",
    "auto",
    "back",
    "car",
    "cars",
    "detail",
    "exterior",
    "for",
    "front",
    "frontview",
    "high",
    "hq",
    "image",
    "images",
    "interior",
    "of",
    "part",
    "parts",
    "photo",
    "photos",
    "picture",
    "pictures",
    "quality",
    "rear",
    "real",
    "shot",
    "side",
    "top",
    "trim",
    "vehicle",
    "view",
    "with",
}


def _default_index() -> dict[str, Any]:
    return {"version": 1, "updated_at": "", "assets": []}


def _now() -> str:
    return datetime.utcnow().isoformat()


def _file_signature(path: Path) -> str:
    stats = path.stat()
    return f"{stats.st_size}:{stats.st_mtime_ns}"


def _keywords(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", (value or "").lower())
        if len(token) > 1 and token not in _GENERIC_KEYWORDS
    }


def _manifest_like_text(asset: dict[str, Any]) -> str:
    labels = " ".join(str(label) for label in asset.get("vision_labels", []) or [])
    parts = [
        asset.get("filename", ""),
        asset.get("query", ""),
        asset.get("relative_path", ""),
        asset.get("vision_description", ""),
        labels,
        asset.get("requirement_key", ""),
    ]
    return " ".join(part for part in parts if part)


def _relative_to_workspace(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(WORKSPACE_ROOT))
    except ValueError:
        return path.name


def _source_run_id(path: Path) -> str:
    try:
        relative_parts = path.resolve().relative_to(RUNS_ROOT).parts
    except ValueError:
        return ""
    return relative_parts[0] if relative_parts else ""


def load_global_index(index_path: str | None = None) -> dict[str, Any]:
    path = Path(index_path or GLOBAL_ASSET_INDEX_PATH)
    if not path.exists():
        return _default_index()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _default_index()
    if not isinstance(raw, dict):
        return _default_index()
    raw.setdefault("version", 1)
    raw.setdefault("updated_at", "")
    raw.setdefault("assets", [])
    if not isinstance(raw["assets"], list):
        raw["assets"] = []
    return raw


def save_global_index(index: dict[str, Any], index_path: str | None = None) -> dict[str, Any]:
    path = Path(index_path or GLOBAL_ASSET_INDEX_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    index["updated_at"] = _now()
    path.write_text(json.dumps(index, indent=2), encoding="utf-8")
    return index


def sync_global_asset_index(index_path: str | None = None, full_scan: bool = False) -> dict[str, Any]:
    index = load_global_index(index_path)
    known_assets = {
        str(asset.get("path", "")): asset
        for asset in index.get("assets", [])
        if isinstance(asset, dict) and asset.get("path")
    }
    seen_paths: set[str] = set()

    if full_scan and WORKSPACE_ROOT.exists():
        for path in WORKSPACE_ROOT.rglob("*"):
            if not path.is_file():
                continue
            if path.resolve() == Path(index_path or GLOBAL_ASSET_INDEX_PATH).resolve():
                continue
            if path.suffix.lower() not in _IMAGE_SUFFIXES:
                continue

            resolved = str(path.resolve())
            seen_paths.add(resolved)
            signature = _file_signature(path)
            existing = known_assets.get(resolved)
            if existing and existing.get("inspection_signature") == signature:
                existing.update({
                    "exists": True,
                    "last_seen_at": _now(),
                    "relative_path": _relative_to_workspace(path),
                    "filename": path.name,
                    "source_run_id": _source_run_id(path),
                })
                continue

            preserved_status = existing.get("status", "unverified") if existing else "unverified"
            preserved_reason = existing.get("reason", "") if existing else ""
            preserved_query = existing.get("query", "") if existing else ""
            preserved_requirement = existing.get("requirement_key", "") if existing else ""
            asset = {
                "path": resolved,
                "filename": path.name,
                "relative_path": _relative_to_workspace(path),
                "query": preserved_query,
                "requirement_key": preserved_requirement,
                "status": preserved_status,
                "reason": preserved_reason,
                "inspection_signature": signature,
                "vision_model": existing.get("vision_model", "") if existing else "",
                "vision_confidence": existing.get("vision_confidence", 0.0) if existing else 0.0,
                "vision_labels": existing.get("vision_labels", []) if existing else [],
                "vision_description": existing.get("vision_description", "") if existing else "",
                "image_kind": existing.get("image_kind", "") if existing else "",
                "is_real_photo": existing.get("is_real_photo", True) if existing else True,
                "origin": existing.get("origin", "global_scan") if existing else "global_scan",
                "source_run_id": _source_run_id(path),
                "exists": True,
                "last_seen_at": _now(),
            }
            if existing:
                existing.clear()
                existing.update(asset)
            else:
                index.setdefault("assets", []).append(asset)
                known_assets[resolved] = asset

    for asset in index.get("assets", []):
        path_value = str(asset.get("path", ""))
        if not path_value:
            continue
        asset["exists"] = path_value in seen_paths

    return save_global_index(index, index_path)


def merge_manifest_into_global_index(
    manifest: dict[str, Any],
    index_path: str | None = None,
) -> dict[str, Any]:
    index = sync_global_asset_index(index_path, full_scan=False)
    known_assets = {
        str(asset.get("path", "")): asset
        for asset in index.get("assets", [])
        if isinstance(asset, dict) and asset.get("path")
    }
    for manifest_asset in manifest.get("assets", []):
        path_value = str(manifest_asset.get("path", ""))
        if not path_value:
            continue
        target = known_assets.get(path_value)
        if target is None:
            target = {}
            index.setdefault("assets", []).append(target)
            known_assets[path_value] = target
        target.update({
            **target,
            **manifest_asset,
            "path": path_value,
            "filename": manifest_asset.get("filename", Path(path_value).name),
            "relative_path": manifest_asset.get("relative_path", _relative_to_workspace(Path(path_value))),
            "source_run_id": manifest_asset.get("source_run_id", _source_run_id(Path(path_value))),
            "exists": Path(path_value).exists(),
            "last_seen_at": _now(),
        })
        if not target.get("requirement_key") and target.get("query"):
            target["requirement_key"] = normalize_image_requirement(target["query"])
    return save_global_index(index, index_path)


def _candidate_score(asset: dict[str, Any], query: str) -> float:
    path_value = str(asset.get("path", ""))
    if not path_value or not asset.get("exists", False):
        return -1.0

    requirement_key = normalize_image_requirement(query)
    asset_requirement = str(asset.get("requirement_key", "")).strip()
    if requirement_key and asset_requirement and asset_requirement != requirement_key:
        return -1.0

    asset_keywords = _keywords(_manifest_like_text(asset))
    specific_query_keywords = _keywords(query)
    specific_overlap = asset_keywords & specific_query_keywords

    if specific_query_keywords and not specific_overlap:
        return -1.0

    score = 0.0
    score += len(specific_overlap) * 10.0
    if asset_requirement == requirement_key and requirement_key:
        score += 18.0

    status = str(asset.get("status", "unverified")).strip().lower()
    if status == "accepted":
        score += 30.0
    elif status == "unverified":
        score += 8.0
    else:
        score -= 10.0

    confidence = float(asset.get("vision_confidence", 0.0) or 0.0)
    score += min(confidence, 1.0) * 10.0

    if asset.get("is_real_photo", True):
        score += 4.0
    if asset.get("image_kind"):
        score += 2.0

    return score


def find_reusable_candidates(
    required_queries: list[str],
    exclude_paths: set[str] | None = None,
    index_path: str | None = None,
) -> list[dict[str, Any]]:
    exclude_paths = exclude_paths or set()
    index = load_global_index(index_path)
    used_paths: set[str] = set(exclude_paths)
    selections: list[dict[str, Any]] = []

    for query in required_queries:
        query = str(query).strip()
        if not query:
            continue
        candidates: list[tuple[float, dict[str, Any]]] = []
        for asset in index.get("assets", []):
            path_value = str(asset.get("path", ""))
            if not path_value or path_value in used_paths:
                continue
            score = _candidate_score(asset, query)
            if score < 15.0:
                continue
            candidates.append((score, asset))

        if not candidates:
            continue

        candidates.sort(key=lambda item: item[0], reverse=True)
        best_score, best_asset = candidates[0]
        if best_score < 15.0:
            continue
        selected = dict(best_asset)
        selected["reuse_for_query"] = query
        selections.append(selected)
        used_paths.add(str(best_asset.get("path", "")))

    return selections


def stage_reusable_assets_for_run(
    manifest_path: str,
    run_id: str,
    required_queries: list[str],
    existing_manifest: dict[str, Any] | None = None,
    index_path: str | None = None,
) -> dict[str, Any]:
    index = sync_global_asset_index(index_path, full_scan=False)
    manifest = existing_manifest or {"assets": []}
    exclude_paths = {
        str(asset.get("path", ""))
        for asset in manifest.get("assets", [])
        if isinstance(asset, dict) and asset.get("path")
    }
    candidates = find_reusable_candidates(
        required_queries,
        exclude_paths=exclude_paths,
        index_path=index_path,
    )

    reused_assets: list[str] = []
    for candidate in candidates:
        path_value = str(candidate.get("path", ""))
        query = str(candidate.get("reuse_for_query", "")).strip()
        requirement_key = normalize_image_requirement(query) if query else candidate.get("requirement_key", "")
        register_asset(
            manifest_path,
            {
                **candidate,
                "path": path_value,
                "query": query or candidate.get("query", ""),
                "requirement_key": requirement_key,
                "origin": "global_reuse",
                "reused_from_global_index": True,
                "reused_at": _now(),
                "vision_fallback": candidate.get("vision_fallback", "")
                or ("global_cache" if candidate.get("status") in {"accepted", "rejected"} else ""),
            },
            run_id=run_id,
        )
        reused_assets.append(path_value)

    return {
        "index": index,
        "reused_assets": reused_assets,
        "reused_count": len(reused_assets),
    }
