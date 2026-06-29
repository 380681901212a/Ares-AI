"""
data_processor — AresAI SkillRegistry

Reads context_data.json (which may contain raw Searcher iteration keys or
already-structured data), normalizes it, and writes a clean JSON back.

Handles both formats:
    - Raw: {"iteration_1_web_results": [...], "iteration_2_web_results": [...]}
    - Clean: {"technical_specifications": {...}, "competitors": [...]}

Config:
    context_data_path:  str  — input path (default: 'workspace/context_data.json')
    output_path:        str  — output path (default: same as input)
    fields_to_extract:  list — specific keys to keep (optional, default: keep all clean keys)

Returns:
    str — path to the processed JSON file, or error string
"""

import json
import os
import re
from pathlib import Path


# ── Keys that should be auto-detected as clean data ───────────────────────────
_KNOWN_CLEAN_KEYS = {
    # Universal Option C keys
    "title", "description", "sections", "verdict", "structured_data",
    # Domain-specific legacy keys
    "technical_specifications", "interior_features", "exterior_features",
    "safety_systems", "competitors", "ukraine_prices", "pricing",
    "pros_and_cons", "warranty", "performance", "fuel_efficiency",
    "engine", "dimensions", "features", "overview", "user_reviews",
    "maintenance", "models", "trim_levels",
}

_ITERATION_PATTERN = re.compile(r"^iteration_\d+")


def _is_raw_iteration_data(context: dict) -> bool:
    """Return True if context contains raw iteration_ keys from Searcher."""
    return any(_ITERATION_PATTERN.match(k) for k in context)


def _extract_from_raw(context: dict) -> dict:
    """
    Extract and merge structured data from raw iteration_ web/image results.
    Falls back to collecting any non-iteration keys as-is.
    """
    clean: dict = {}

    for key, val in context.items():
        # Skip iteration image results
        if _ITERATION_PATTERN.match(key) and "image" in key:
            continue

        if _ITERATION_PATTERN.match(key):
            # Web results: list of {"url", "content"} dicts
            if isinstance(val, list):
                for item in val:
                    if isinstance(item, dict):
                        # Items from InfoChecker clean_summary may have clean keys
                        for sub_key, sub_val in item.items():
                            if sub_key in _KNOWN_CLEAN_KEYS:
                                if sub_key not in clean:
                                    clean[sub_key] = sub_val
                                elif isinstance(sub_val, dict) and isinstance(clean[sub_key], dict):
                                    clean[sub_key].update(sub_val)
                                elif isinstance(sub_val, list) and isinstance(clean[sub_key], list):
                                    clean[sub_key].extend(
                                        x for x in sub_val if x not in clean[sub_key]
                                    )
        else:
            # Already a clean key — keep it
            if key in _KNOWN_CLEAN_KEYS or key not in clean:
                clean[key] = val

    return clean


def _normalize(context: dict) -> dict:
    """Top-level normalization: handle raw vs. clean data."""
    if _is_raw_iteration_data(context):
        clean = _extract_from_raw(context)
        for k, v in context.items():
            if not _ITERATION_PATTERN.match(k) and k not in clean:
                clean[k] = v
        return clean if clean else context
    
    # Option C format: {title, description, sections (list)} — pass through as-is
    if "sections" in context and isinstance(context.get("sections"), list):
        return context
        
    # Legacy format: sections is a dict — expand to top level
    if "sections" in context and isinstance(context.get("sections"), dict):
        result = {}
        if "title" in context:
            result["title"] = context["title"]
        if "description" in context:
            result["description"] = context["description"]
        result.update(context["sections"])
        return result
        
    # Already has known keys — return as-is
    known_found = any(k in _KNOWN_CLEAN_KEYS for k in context)
    if known_found:
        return context
        
    # Last resort: return as-is (don't collapse to overview string)
    return context


def execute(config: dict, workdir: str = ".") -> str:
    """Normalize context_data.json and write the clean version."""
    ctx_rel = config.get("context_data_path", "workspace/context_data.json")
    out_rel = config.get("output_path", ctx_rel)
    fields  = config.get("fields_to_extract")

    ctx_abs = os.path.join(workdir, ctx_rel)
    out_abs = os.path.join(workdir, out_rel)

    if not os.path.exists(ctx_abs):
        return f"Error: context_data.json not found at {ctx_abs}"

    with open(ctx_abs, "r", encoding="utf-8") as f:
        context = json.load(f)

    normalized = _normalize(context)

    # Optional field filter
    if fields:
        normalized = {k: v for k, v in normalized.items() if k in fields}

    os.makedirs(os.path.dirname(out_abs), exist_ok=True)
    with open(out_abs, "w", encoding="utf-8") as f:
        json.dump(normalized, f, indent=2, ensure_ascii=False)

    keys_found = list(normalized.keys())
    print(f"[data_processor] ✅ Normalized {len(keys_found)} keys: {keys_found}")
    return out_rel
