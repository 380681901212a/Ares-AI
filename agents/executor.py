"""Executor Agent — AresAI 3.0.

Changes from v2.0:
- SKILL PATH: when skill_id+skill_config are set → execute pre-verified skill module directly.
              No LLM code generation needed. Near 100% reliability.
- CODE DOCTOR: between retry attempts, fixes the broken code surgically instead of
               re-submitting the same prompt.
- PATTERN LEARNING: saves successful generated code as patterns for future Blacksmith use.
- STDLIB INJECTION: always prepends import os/json/sys/re/Path to generated code.
- SKIP ON 3 FAILURES: does not block the whole pipeline; logs the error and advances.
"""

import json
import os
import re
import threading
from pathlib import Path

from langchain_core.prompts import ChatPromptTemplate

from agents.code_doctor import fix as code_doctor_fix
from tools.code_audit import static_code_audit
from llm_config import get_llm
from schemas import CodeGenerationSchema
from state import AresState
from tools.sandbox import execute_python_code
from tools.run_logger import RunLogger

_REGISTRY_PATH  = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "registry", "agents.json"))
_PATTERNS_DIR   = Path(__file__).parent.parent / "registry" / "patterns"
_REGISTRY_LOCK  = threading.Lock()

# Skill registry (imported lazily so missing module doesn't crash on import)
try:
    from tools.skill_registry import execute_skill, skill_exists
    _SKILL_REGISTRY_OK = True
except Exception:
    _SKILL_REGISTRY_OK = False
    def skill_exists(s): return False
    def execute_skill(s, c, w): return f"Error: SkillRegistry not available."


# ── Helpers ────────────────────────────────────────────────────────────────────

def _extract_code(raw: str) -> str:
    match = re.search(r"```python\s*\n(.*?)\n```", raw, re.DOTALL)
    if match:
        return match.group(1).strip()
    return raw.strip()


_STDLIB_HEADER = (
    "import os\nimport json\nimport sys\nimport re\n"
    "from pathlib import Path\n\n"
)

def _inject_stdlib(code: str) -> str:
    """Prepend stdlib imports if not already in the first 8 lines."""
    first_lines = "\n".join(code.split("\n")[:8])
    if "import os" not in first_lines:
        return _STDLIB_HEADER + code
    return code


def _save_to_registry(profile: dict) -> None:
    """Thread-safe registry write."""
    with _REGISTRY_LOCK:
        os.makedirs(os.path.dirname(_REGISTRY_PATH), exist_ok=True)
        existing_agents = []
        if os.path.exists(_REGISTRY_PATH):
            try:
                with open(_REGISTRY_PATH, "r", encoding="utf-8") as handle:
                    existing_agents = json.load(handle)
            except json.JSONDecodeError:
                existing_agents = []

        clean_profile = {
            "agent_name":         profile.get("agent_name", ""),
            "agent_category":     profile.get("agent_category", "utility"),
            "agent_capabilities": profile.get("agent_capabilities", []),
            "system_prompt":      profile.get("system_prompt", ""),
            "required_libraries": profile.get("required_libraries", []),
            "few_shot_examples":  profile.get("few_shot_examples", []),
        }

        replaced = False
        for index, existing in enumerate(existing_agents):
            if existing.get("agent_name") == clean_profile["agent_name"]:
                existing_agents[index] = clean_profile
                replaced = True
                break
        if not replaced:
            existing_agents.append(clean_profile)

        with open(_REGISTRY_PATH, "w", encoding="utf-8") as handle:
            json.dump(existing_agents, handle, indent=4, ensure_ascii=False)



def _check_output_quality(result: str, task_type: str, workdir: str) -> str | None:
    """
    Returns None if quality OK, or error description string if quality check fails.
    Called after every skill execution.
    """
    if not result or result.startswith("Error:"):
        return result or "Skill returned empty result"
    
    # For word_creator: check file exists and is not suspiciously small
    if task_type == "document" and result.endswith(".docx"):
        abs_path = os.path.join(workdir, result)
        if not os.path.exists(abs_path):
            return f"Output file not found: {result}"
        size_kb = os.path.getsize(abs_path) / 1024
        if size_kb < 8:
            return f"Output file suspiciously small ({size_kb:.1f} KB) — likely empty document"
    
    # For data_processor: check output JSON has useful keys
    if task_type == "data_analysis" and result.endswith(".json"):
        abs_path = os.path.join(workdir, result)
        if os.path.exists(abs_path):
            try:
                with open(abs_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if len(data) == 0:
                    return "data_processor returned empty JSON"
                # Check if all values are empty strings
                non_empty = sum(1 for v in data.values() if v)
                if non_empty == 0:
                    return "data_processor returned JSON with all empty values"
            except Exception as e:
                return f"data_processor output is invalid JSON: {e}"
    
    return None  # quality OK

def _save_successful_pattern(task_type: str, skill_config: dict) -> None:
    """Save a successful skill config as a pattern for future Blacksmith few-shot examples."""
    _PATTERNS_DIR.mkdir(parents=True, exist_ok=True)
    pattern_file = _PATTERNS_DIR / f"{task_type}.json"
    
    patterns = []
    if pattern_file.exists():
        try:
            with open(pattern_file, "r", encoding="utf-8") as f:
                patterns = json.load(f)
        except Exception:
            patterns = []
    
    # Check for duplicate (same keys structure)
    config_keys = sorted(skill_config.keys())
    for p in patterns:
        if sorted(p.get("config", {}).keys()) == config_keys:
            p["success_count"] = p.get("success_count", 0) + 1
            p["config"] = skill_config  # update with latest successful config
            break
    else:
        patterns.append({
            "task_type": task_type,
            "config": skill_config,
            "success_count": 1,
            "code": json.dumps(skill_config, ensure_ascii=False),
        })
    
    # Keep only top 10 patterns per task type
    patterns = sorted(patterns, key=lambda x: x.get("success_count", 0), reverse=True)[:10]
    
    with open(pattern_file, "w", encoding="utf-8") as f:
        json.dump(patterns, f, indent=2, ensure_ascii=False)
    
    print(f"[Executor] 📚 Pattern saved for '{task_type}' (total: {len(patterns)})")


def _save_pattern(task_type: str, code: str) -> None:
    """Save a successful code pattern for future Blacksmith few-shot use."""
    try:
        _PATTERNS_DIR.mkdir(parents=True, exist_ok=True)
        pattern_file = _PATTERNS_DIR / f"{task_type}.json"
        patterns: list = []
        if pattern_file.exists():
            with open(pattern_file, "r", encoding="utf-8") as f:
                patterns = json.load(f)

        # Check if identical code already saved
        for p in patterns:
            if p.get("code", "") == code:
                p["success_count"] = p.get("success_count", 1) + 1
                break
        else:
            patterns.append({"code": code, "success_count": 1})

        # Keep only last 10 patterns
        patterns = sorted(patterns, key=lambda x: x.get("success_count", 0), reverse=True)[:10]
        with open(pattern_file, "w", encoding="utf-8") as f:
            json.dump(patterns, f, indent=2, ensure_ascii=False)
        print(f"[Executor] 📚 Pattern saved for task_type='{task_type}'.")
    except Exception as e:
        print(f"[Executor] Pattern save failed (non-critical): {e}")


def _success_return(
    state: AresState,
    output_body: str,
    clean_code: str,
    task_type: str,
) -> dict:
    """Build the success return dict and advance the sub-agent index."""
    sub_agent_plan = state.get("sub_agent_plan") or []
    current_idx    = state.get("current_sub_agent_index") or 0
    sub_agent_results = state.get("sub_agent_results") or {}
    current_task_meta = sub_agent_plan[current_idx] if current_idx < len(sub_agent_plan) else {}
    task_id  = current_task_meta.get("task_id", f"task_{current_idx}")
    new_index = current_idx + 1
    all_done  = (not sub_agent_plan) or (new_index >= len(sub_agent_plan))

    new_results = dict(sub_agent_results)
    new_results[task_id] = output_body

    RunLogger(state["run_id"]).log("executor", "skill_executed", {"skill": state.get("skill_id"), "result": "ok"})

    # Save pattern (Фаза 3 — Pattern Learning)
    if clean_code:
        _save_pattern(task_type, clean_code)

    print(f"✅ [Executor] Sub-agent '{task_id}' completed. Index {current_idx} → {new_index}.")
    if all_done:
        print("🏁 [Executor] All sub-agents done — signaling completion.")

    return {
        "sub_agent_results":       new_results,
        "current_sub_agent_index": new_index,
        "current_agent_profile":   {},
        "current_specialist":      "",
        "skill_id":                None,
        "skill_config":            None,
        "raw_generated_code":      None,
        "qa_passed":               False,
        "errors":                  [],
        "feedback_for_blacksmith": "",
        "final_result":            output_body,
        "execution_attempts":      0,
        "last_execution_error":    "",
        "agent_needs":             "",
        "completed":               all_done,
    }


def _skip_return(state: AresState, failure_result: str, new_attempts: int) -> dict:
    """Skip the current sub-agent after 3 failures and advance pipeline."""
    sub_agent_plan    = state.get("sub_agent_plan") or []
    current_idx       = state.get("current_sub_agent_index") or 0
    sub_agent_results = state.get("sub_agent_results") or {}
    current_task_meta = sub_agent_plan[current_idx] if current_idx < len(sub_agent_plan) else {}
    task_id   = current_task_meta.get("task_id", f"task_{current_idx}")
    new_index = current_idx + 1
    all_done  = (not sub_agent_plan) or (new_index >= len(sub_agent_plan))

    new_results = dict(sub_agent_results)
    new_results[task_id] = f"[FAILED after {new_attempts} attempts] {failure_result}"
    RunLogger(state["run_id"]).log("executor", "code_error", {"error": failure_result, "attempt": new_attempts})

    print(f"❌ [Executor] Sub-agent '{task_id}' failed {new_attempts}x — skipping, advancing to {new_index}.")
    return {
        "sub_agent_results":       new_results,
        "current_sub_agent_index": new_index,
        "current_agent_profile":   {},
        "current_specialist":      "",
        "skill_id":                None,
        "skill_config":            None,
        "raw_generated_code":      None,
        "qa_passed":               False,
        "errors":                  [],
        "feedback_for_blacksmith": "",
        "final_result":            f"[Sub-agent '{task_id}' skipped after 3 failures]",
        "execution_attempts":      0,
        "last_execution_error":    "",
        "completed":               all_done,
    }


# ── Main entry point ───────────────────────────────────────────────────────────


def _get_ready_tasks(plan: list, results: dict) -> list:
    """Повертає задачі чиї всі залежності вже виконані."""
    ready = []
    for task in plan:
        tid = task.get("task_id", "")
        if not tid or tid in results:
            continue
        deps = task.get("input_from", [])
        if all(d in results for d in deps):
            ready.append(task)
    return ready

def execution_agent(state: AresState) -> dict:
    profile     = state.get("current_agent_profile", {})
    skill_id_st = state.get("skill_id")
    skill_config = state.get("skill_config")
    attempts    = state.get("execution_attempts", 0)

    # Determine current task metadata
    sub_agent_plan    = state.get("sub_agent_plan") or []
    current_idx       = state.get("current_sub_agent_index") or 0
    current_task_meta = sub_agent_plan[current_idx] if current_idx < len(sub_agent_plan) else {}
    task_type         = current_task_meta.get("task_type", "utility")
    workdir           = state.get("run_root_dir", ".")

    # ── SKILL PATH (AresAI 3.0) ───────────────────────────────────────────────
    if _SKILL_REGISTRY_OK and skill_id_st and skill_config is not None:
        skill_id_to_run = skill_id_st
        print(f"⚡ [Executor] Running SKILL: '{skill_id_to_run}' (no code generation needed)")
        try:
            result_str = execute_skill(skill_id_to_run, skill_config, workdir)
        except Exception as exc:
            import traceback
            result_str = f"Error: Skill '{skill_id_to_run}' crashed.\n{traceback.format_exc()}"

        quality_error = _check_output_quality(result_str, task_type, workdir)
        if quality_error:
            result_str = f"Error: Quality check failed: {quality_error}"
            
        if result_str.startswith("Error:"):
            new_attempts = attempts + 1
            RunLogger(state["run_id"]).log("executor", "code_error", {"error": result_str, "attempt": new_attempts})
            if new_attempts >= 3:
                return _skip_return(state, result_str, new_attempts)
            print(f"[Executor] Skill attempt {new_attempts} failed: {result_str[:200]}")
            return {
                "execution_attempts": new_attempts,
                "last_execution_error": result_str,
                "completed": False,
            }

        _save_successful_pattern(task_type, skill_config)
        return _success_return(state, result_str, clean_code="", task_type=task_type)

    # ── CODE GENERATION PATH (legacy / fallback) ──────────────────────────────
    if not profile or not profile.get("agent_name"):
        return {
            "agent_needs": "No agent profile loaded. Core must route to Blacksmith first.",
            "completed": False,
            "last_execution_error": "",
        }

    llm = get_llm()
    code_llm = llm.with_structured_output(CodeGenerationSchema)

    # Build assets string
    manifest_assets  = state.get("asset_manifest", {}).get("assets", [])
    accepted_details = []
    for asset in manifest_assets:
        if asset.get("status") == "accepted":
            asset_path_abs = asset.get("path")
            rel_path = f"workspace/{os.path.basename(asset_path_abs)}" if asset_path_abs else ""
            accepted_details.append({
                "path":        rel_path,
                "query":       asset.get("query"),
                "description": asset.get("vision_description") or "",
            })
    assets_str = json.dumps(accepted_details, indent=2)

    # Context data preview
    context_data_path = state.get("context_data_path", "")
    keys_str = "No valid JSON structure found in context data."
    if context_data_path and os.path.exists(context_data_path):
        try:
            with open(context_data_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            preview = json.dumps(data, indent=2, ensure_ascii=False)
            keys_str = "Context Data Preview:\n" + preview[:6000]
            if len(preview) > 6000:
                keys_str += "\n... (truncated)"
        except Exception:
            pass

    sub_agent_results = state.get("sub_agent_results") or {}
    prior_results_str = (
        json.dumps(sub_agent_results, indent=2, ensure_ascii=False)
        if sub_agent_results else "None (this is the first sub-agent)."
    )

    examples     = profile.get("few_shot_examples", [])
    examples_str = "\n".join(examples) if isinstance(examples, list) else str(examples)
    last_error   = state.get("last_execution_error", "")

    system_message = (
        "You are {agent_name}. {system_prompt}. Examples: {few_shot_examples}. "
        "Your sub-task: {sub_task}. Original task: {original_task}. "
        "Write the final Python code to solve this sub-task. "
        "Output ONLY valid JSON matching CodeGenerationSchema. "
        "CRITICAL: print the final success message or result at the end of the script. "
        "CODE FORMATTING RULE: wrap final Python code in markdown blocks (```python ... ```). "
        "DOCUMENT QUALITY RULE: Never use placeholder text like '(BMW data)'. "
        "Open workspace/context_data.json, parse it, and write REAL values into the document. "
        "PYTHON TEXT RULE: Do NOT hardcode giant paragraphs. Format dynamically from JSON variables. "
        "PATHS RULE: USE ONLY RELATIVE PATHS ('workspace/...'). NEVER use absolute paths.\n"
        "IMPORTS RULE: Include ALL imports at the top. "
        "WORD DOCUMENT HELPER RULE: If your task involves creating a Word (.docx) document, "
        "you MUST use the verified helper module. "
        "Add project root to sys.path: import sys; sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))) \n"
        "Then import: from tools.docx_helper import (add_premium_heading, add_premium_table, "
        "add_image_safe, add_divider, add_body_text, add_bullet, set_doc_margins, set_default_style)\n"
        "DO NOT write raw doc.add_table() / doc.add_picture() calls — ALWAYS use the helpers.\n"
        "Available Local Assets (use EXACT relative paths starting with 'workspace/'):\n{assets}\n"
        "The structured context file is at workspace/context_data.json.\n"
        "Context Data Keys and Values: {gathered_info}\n"
        "Prior Sub-Agent Results (exact outputs from previous pipeline stages): {prior_results}\n"
        "IMAGE ERROR HANDLING RULE: the add_image_safe() helper already handles errors gracefully."
    )

    if last_error:
        system_message += (
            "\nYour previous code failed with this error: {last_execution_error}. "
            "Use thought_process to explain the failure and precisely how you are fixing it."
        )

    prompt = ChatPromptTemplate.from_messages([("system", system_message)])
    chain  = prompt | code_llm

    invoke_args = {
        "agent_name":      profile.get("agent_name", "Executor"),
        "system_prompt":   profile.get("system_prompt", ""),
        "few_shot_examples": examples_str,
        "sub_task":        state.get("sub_task", state["original_task"]),
        "original_task":   state["original_task"],
        "assets":          assets_str,
        "gathered_info":   keys_str,
        "prior_results":   prior_results_str,
    }
    if last_error:
        invoke_args["last_execution_error"] = last_error

    print(f"⚡ [Executor] Writing code for '{profile.get('agent_name', 'Agent')}' (attempt {attempts+1}/3)...")
    result_schema = chain.invoke(invoke_args)
    clean_code    = _inject_stdlib(_extract_code(result_schema.code))

    # Static security audit
    static_issues = static_code_audit(clean_code)
    if static_issues:
        current_error = "Error: QA preflight blocked execution.\n" + "\n".join(static_issues)
        execution_result = None
    else:
        execution_result = execute_python_code(
            clean_code,
            profile.get("required_libraries", []),
            workdir=workdir,
        )
        current_error = ""
        if execution_result.startswith("Error"):
            current_error = execution_result
        else:
            output_body = execution_result.removeprefix("Success:\n").strip()
            if not output_body:
                current_error = (
                    "Error: Script ran but printed no output. "
                    "You MUST use print() to output the final result."
                )

    # ── SUCCESS ────────────────────────────────────────────────────────────────
    if not static_issues and not current_error:
        output_body = (execution_result or "").removeprefix("Success:\n").strip()

        # Save profile to registry
        updated_profile = {**profile, "few_shot_examples": [f"```python\n{clean_code}\n```"]}
        _save_to_registry(updated_profile)

        return _success_return(state, output_body, clean_code=clean_code, task_type=task_type)

    # ── FAILURE ────────────────────────────────────────────────────────────────
    failure_result = current_error or "Error: QA preflight blocked execution."
    new_attempts   = attempts + 1

    RunLogger(state["run_id"]).log("executor", "code_error", {"error": failure_result, "attempt": new_attempts})

    # Save failed code so Code Doctor can fix it on next attempt
    state_update: dict = {
        "execution_attempts":  new_attempts,
        "last_execution_error": failure_result,
        "raw_generated_code":   clean_code,
        "completed":            False,
        "agent_needs":          "",
    }

    if new_attempts >= 3:
        return _skip_return(state, failure_result, new_attempts)

    # Apply Code Doctor fix for next attempt
    prev_code = state.get("raw_generated_code", "")
    if prev_code and failure_result:
        print("[Executor] 🩺 Calling Code Doctor to fix code before next attempt...")
        fixed = code_doctor_fix(prev_code, failure_result)
        if fixed and fixed != prev_code:
            state_update["raw_generated_code"] = fixed
            # Inject fixed code as the error context so executor uses it next round
            state_update["last_execution_error"] = (
                f"{failure_result}\n\n[Code Doctor applied a fix — retry with corrected code]"
            )

    return state_update
