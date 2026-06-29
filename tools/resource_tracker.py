"""Resource tracker for run-scoped Ares workspaces."""

from __future__ import annotations

import pathlib
from typing import Dict, List

from tools.asset_manifest import load_manifest, summarize_manifest
from tools.runtime_paths import WORKSPACE_ROOT

_DEFAULT_WORKSPACE = WORKSPACE_ROOT

_EXT_MAP: Dict[str, str] = {
    ".png": "images",
    ".jpg": "images",
    ".jpeg": "images",
    ".gif": "images",
    ".webp": "images",
    ".bmp": "images",
    ".docx": "documents",
    ".pdf": "documents",
    ".txt": "documents",
    ".md": "documents",
    ".json": "data",
    ".csv": "data",
    ".xlsx": "data",
    ".xml": "data",
    ".py": "scripts",
    ".js": "scripts",
    ".sh": "scripts",
}


def _resolve_workspace_dir(run_workspace_dir: str | None = None) -> pathlib.Path:
    if run_workspace_dir:
        return pathlib.Path(run_workspace_dir)
    return _DEFAULT_WORKSPACE


def scan_workspace(run_workspace_dir: str | None = None) -> Dict[str, List[str]]:
    result: Dict[str, List[str]] = {
        "images": [],
        "documents": [],
        "data": [],
        "scripts": [],
        "other": [],
    }

    workspace = _resolve_workspace_dir(run_workspace_dir)
    if not workspace.exists():
        return result

    for entry in sorted(workspace.iterdir()):
        if not entry.is_file():
            continue
        ext = entry.suffix.lower()
        category = _EXT_MAP.get(ext, "other")
        result[category].append(f"workspace/{entry.name}")

    return result


def scan_workspace_summary(
    run_workspace_dir: str | None = None,
    manifest_path: str | None = None,
    required_queries: list[str] | None = None,
) -> Dict[str, object]:
    full = scan_workspace(run_workspace_dir)
    summary: Dict[str, object] = {}
    for category, files in full.items():
        summary[category] = {
            "count": len(files),
            "samples": files[:3],
        }

    if manifest_path:
        manifest = load_manifest(manifest_path)
        summary["asset_manifest"] = summarize_manifest(manifest, required_queries)

    return summary
