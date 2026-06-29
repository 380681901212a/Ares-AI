import os
import re
from datetime import datetime
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from state import AresState
from tools.asset_manifest import (
    ensure_manifest,
    load_manifest,
    normalize_image_requirement,
    normalize_query,
    register_discovered_files,
    summarize_manifest,
    update_asset,
)
from tools.global_asset_index import merge_manifest_into_global_index, stage_reusable_assets_for_run
from tools.ollama_runtime import get_vision_model_name
from tools.vision_analyzer import analyze_image_with_vision

_MIN_FILE_BYTES = 512
_MIN_DIMENSION = 64
_MIN_PHOTO_SIZE_BYTES = 40_000
_BLOCKED_DOMAINS = {"devicons", "jsdelivr.net", "shields.io", "flaticon", "svgrepo", "cdnjs"}
_BLOCKED_EXTENSIONS = {".svg", ".ico", ".gif"}
_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "photo",
    "photos",
    "image",
    "images",
    "real",
    "high",
    "resolution",
    "picture",
    "pictures",
    "of",
    "a",
    "an",
}


def _keywords(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", value.lower())
        if len(token) > 2 and token not in _STOPWORDS
    }


def _best_requirement_match(asset: dict, required_queries: list[str]) -> tuple[str, str]:
    if not required_queries:
        return asset.get("query", ""), asset.get("requirement_key", "")

    asset_text = " ".join(
        part
        for part in [
            asset.get("filename", ""),
            asset.get("query", ""),
            asset.get("relative_path", ""),
        ]
        if part
    )
    asset_keywords = _keywords(asset_text)
    best_query = ""
    best_score = 0.0
    for query in required_queries:
        query_keywords = _keywords(query)
        if not query_keywords:
            continue
        overlap = asset_keywords & query_keywords
        score = len(overlap) / len(query_keywords)
        if len(overlap) >= 2 and score > best_score:
            best_query = query
            best_score = score
    if best_query:
        return best_query, normalize_image_requirement(best_query)
    return asset.get("query", ""), asset.get("requirement_key", "")


def _is_valid_photo(file_path: str, file_size: int, source_url: str = "") -> bool:
    ext = Path(file_path).suffix.lower()
    if ext in _BLOCKED_EXTENSIONS:
        return False
    if file_size < _MIN_PHOTO_SIZE_BYTES:
        return False
    if any(domain in source_url for domain in _BLOCKED_DOMAINS):
        return False
    return True

def _inspect_image(path: Path, source_url: str = "") -> tuple[str, str, dict]:
    if not path.exists():
        return "rejected", "File is missing from disk.", {}
    file_size = path.stat().st_size
    if file_size < _MIN_FILE_BYTES:
        return "rejected", "File is too small to be a useful asset.", {
            "size_bytes": file_size,
        }
    if not _is_valid_photo(str(path), file_size, source_url):
        return "rejected", "File failed domain, extension, or photo size validation.", {
            "size_bytes": file_size,
        }

    try:
        with Image.open(path) as img:
            img.load()
            width, height = img.size
            image_format = img.format or path.suffix.lstrip(".").upper()
    except (UnidentifiedImageError, OSError) as exc:
        return "rejected", f"Failed to open image: {exc}", {}

    metadata = {
        "width": width,
        "height": height,
        "format": image_format,
        "size_bytes": path.stat().st_size,
    }
    if width < _MIN_DIMENSION or height < _MIN_DIMENSION:
        return "rejected", "Image dimensions are too small.", metadata
    return "accepted", "", metadata


def _file_signature(path: Path) -> str:
    stats = path.stat()
    return f"{stats.st_size}:{stats.st_mtime_ns}"


def asset_inspector_agent(state: AresState) -> dict:
    manifest_path = state["asset_manifest_path"]
    run_workspace_dir = state["run_workspace_dir"]
    run_id = state["run_id"]
    required_queries = state.get("asset_requirements") or []

    print(f"👁️ [Inspector] Sifting through downloaded media assets...")

    ensure_manifest(manifest_path, run_id)
    manifest = register_discovered_files(manifest_path, run_workspace_dir, run_id)
    if required_queries:
        stage_reusable_assets_for_run(
            manifest_path,
            run_id,
            required_queries,
            existing_manifest=manifest,
            index_path=state.get("global_asset_index_path", ""),
        )
        manifest = load_manifest(manifest_path)

    for asset in manifest.get("assets", []):
        asset_path = Path(asset.get("path", ""))
        matched_query, matched_requirement_key = _best_requirement_match(asset, required_queries)
        signature = _file_signature(asset_path) if asset_path.exists() else ""

        already_inspected = (
            asset.get("inspection_signature") == signature
            and asset.get("status") in {"accepted", "rejected"}
            and (
                not required_queries
                or asset.get("vision_model") == get_vision_model_name()
                or asset.get("vision_fallback") in {"heuristic", "global_cache"}
                or asset.get("reused_from_global_index")
            )
        )
        if already_inspected:
            if matched_requirement_key and asset.get("requirement_key") != matched_requirement_key:
                update_asset(
                    manifest_path,
                    str(asset_path),
                    query=matched_query,
                    requirement_key=matched_requirement_key,
                )
            continue

        status, reason, metadata = _inspect_image(asset_path, source_url=asset.get('source_url', ''))
        inspection_updates = {
            "query": matched_query,
            "requirement_key": matched_requirement_key,
            "status": status,
            "reason": reason,
            "inspected_at": datetime.utcnow().isoformat() if os.path.exists(manifest_path) else "",
            "inspection_signature": signature,
            "vision_model": "",
            "vision_confidence": 0.0,
            "vision_labels": [],
            "vision_description": "",
            "vision_reason": "",
            "vision_fallback": "",
            **metadata,
        }

        if status == "accepted" and required_queries:
            candidate_queries = [matched_query] if matched_query else list(required_queries)
            vision_result = analyze_image_with_vision(str(asset_path), candidate_queries)
            if vision_result.get("ok"):
                decision = vision_result.get("decision", {})
                best_query = str(decision.get("best_match_query", "")).strip()
                if best_query:
                    matched_query = best_query
                    matched_requirement_key = normalize_image_requirement(best_query)
                is_relevant = bool(decision.get("is_relevant", False))
                confidence = float(decision.get("confidence", 0) or 0)
                inspection_updates.update({
                    "query": matched_query,
                    "requirement_key": matched_requirement_key,
                    "status": "accepted" if is_relevant else "rejected",
                    "reason": str(decision.get("reason", "")).strip(),
                    "vision_model": vision_result.get("model", ""),
                    "vision_confidence": confidence,
                    "vision_labels": decision.get("labels", []),
                    "vision_description": decision.get("short_description", ""),
                    "vision_reason": str(decision.get("reason", "")).strip(),
                    "image_kind": decision.get("image_kind", ""),
                    "is_real_photo": decision.get("is_real_photo", True),
                })
            else:
                fallback_status = "accepted" if matched_requirement_key else "rejected"
                fallback_reason = vision_result.get("error", "Vision analysis failed.")
                inspection_updates.update({
                    "status": fallback_status,
                    "reason": fallback_reason,
                    "vision_model": vision_result.get("model", ""),
                    "vision_fallback": "heuristic",
                    "vision_reason": fallback_reason,
                })

        update_asset(
            manifest_path,
            str(asset_path),
            **inspection_updates,
        )

    manifest = load_manifest(manifest_path)
    merge_manifest_into_global_index(
        manifest,
        index_path=state.get("global_asset_index_path", ""),
    )
    summary = summarize_manifest(manifest, required_queries)

    return {
        "asset_manifest": manifest,
        "asset_manifest_summary": summary,
        "accepted_asset_paths": summary["accepted_paths"],
        "missing_image_queries": summary["missing_queries"],
        "agent_needs": "Need more matching images." if summary["missing_queries"] else "",
        "feedback_for_searcher": "",
        "sub_task": state.get("sub_task", ""),
        "current_specialist": state.get("current_specialist", ""),
        "gathered_info": state.get("gathered_info", ""),
    }
