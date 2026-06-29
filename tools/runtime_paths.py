"""Helpers for creating and resolving run-scoped workspace paths."""

from __future__ import annotations

from datetime import datetime
import pathlib
import uuid

BASE_DIR = pathlib.Path(__file__).parent.parent.resolve()
WORKSPACE_ROOT = BASE_DIR / "workspace"
RUNS_ROOT = WORKSPACE_ROOT / "runs"
GLOBAL_ASSET_INDEX_PATH = WORKSPACE_ROOT / "global_asset_index.json"


def make_run_id() -> str:
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    suffix = uuid.uuid4().hex[:8]
    return f"{timestamp}_{suffix}"


def build_run_paths(run_id: str) -> dict[str, str]:
    run_root = RUNS_ROOT / run_id
    run_workspace = run_root / "workspace"
    return {
        "run_id": run_id,
        "run_root_dir": str(run_root),
        "run_workspace_dir": str(run_workspace),
        "context_data_path": str(run_workspace / "context_data.json"),
        "asset_manifest_path": str(run_root / "asset_manifest.json"),
        "downloaded_urls_path": str(run_root / "downloaded_urls.txt"),
        "global_asset_index_path": str(GLOBAL_ASSET_INDEX_PATH),
    }


def ensure_run_paths(run_id: str) -> dict[str, str]:
    paths = build_run_paths(run_id)
    pathlib.Path(paths["run_root_dir"]).mkdir(parents=True, exist_ok=True)
    pathlib.Path(paths["run_workspace_dir"]).mkdir(parents=True, exist_ok=True)
    return paths
