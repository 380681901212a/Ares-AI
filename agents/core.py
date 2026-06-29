"""Core Supervisor — the brain of Ares AI Ecosystem v2.0.

AresAI 2.0 Changes:
- Fully deterministic routing (no LLM fallback needed).
- Supports Multi-Agent Pipeline: iterates through sub_agent_plan sequentially.
- Granular retry: only the failed sub-agent is re-run.
- Fixed: false-completion bug (final_result truthy check replaced by explicit flags).
- Fixed: QA loop protection (max 3 attempts before escalating to human).
- Fixed: max_steps no longer overridden on step 0.
- Fixed: registry fast-match removed (registry cleared for AresAI 2.0 fresh start).
"""

import json
import os
from pathlib import Path

from state import AresState
from tools.asset_manifest import ensure_manifest, summarize_manifest
from tools.resource_tracker import scan_workspace, scan_workspace_summary

_MEMORY_WINDOW = 5
_MAX_QA_ATTEMPTS = 3
_MAX_SEARCHER_LOOPS = 3


from tools.run_logger import RunLogger

def _core_supervisor_internal(state: AresState) -> dict:
    res = _core_supervisor(state)
    if "next_node" in res:
        RunLogger(state["run_id"]).log("core", "routing", {"next": res["next_node"]})
    return res

def _core_supervisor(state: AresState) -> dict:
    step_count = state.get("step_count", 0)
    max_steps = state.get("max_steps", 35)
    core_memory = state.get("core_memory") or []
    last_execution_error = (state.get("last_execution_error") or "").strip()

    needs_human_help = state.get("needs_human_help", False)
    human_response = state.get("human_response", "")
    task_to_evaluate = state["original_task"]
    human_updates = {}

    # ── [1] Human-in-the-loop ─────────────────────────────────────────────────
    if needs_human_help and not human_response:
        print("[Core] Waiting for human input.")
        return {"next_node": "end", "step_count": step_count}

    if needs_human_help and human_response:
        task_to_evaluate = state["original_task"] + f"\n[User Clarification]: {human_response}"
        core_memory = core_memory + ["Human clarified the task."]
        human_updates = {
            "original_task": task_to_evaluate,
            "human_response": "",
            "needs_human_help": False,
            "paused_by": "",
            "core_memory": core_memory,
        }

    # ── [2] Step limit ────────────────────────────────────────────────────────
    if step_count >= max_steps:
        print(f"[Core] Step limit reached ({step_count}/{max_steps}).")
        return {"next_node": "end", "step_count": step_count, **human_updates}

    # ── Load workspace state ──────────────────────────────────────────────────
    manifest = ensure_manifest(state["asset_manifest_path"], state["run_id"])
    asset_requirements = state.get("asset_requirements") or []
    manifest_summary = summarize_manifest(manifest, asset_requirements)
    available_resources = scan_workspace(state["run_workspace_dir"])
    resources_for_llm = scan_workspace_summary(
        state["run_workspace_dir"],
        manifest_path=state["asset_manifest_path"],
        required_queries=asset_requirements,
    )

    _common = {
        "available_resources": available_resources,
        "asset_manifest": manifest,
        "asset_manifest_summary": manifest_summary,
        **human_updates,
    }

    # ── [3] First step — requirements check ───────────────────────────────────
    if step_count == 0:
        return {
            **_common,
            "next_node": "requirements",
            "step_count": 1,
            "max_steps": max_steps,  # preserve original value, never override
            "core_memory": ["[System] AresAI 2.0 — Initial requirements check."],
            "sub_agent_plan": [],
            "current_sub_agent_index": 0,
            "sub_agent_results": {},
            "current_agent_profile": {},
            "qa_passed": False,
            "errors": [],
        }

    # ── Pipeline state ────────────────────────────────────────────────────────
    sub_agent_plan = state.get("sub_agent_plan") or []
    current_index = state.get("current_sub_agent_index") or 0
    sub_agent_results = state.get("sub_agent_results") or {}
    current_profile = state.get("current_agent_profile") or {}
    qa_passed = bool(state.get("qa_passed", False))
    errors = state.get("errors") or []
    feedback_for_blacksmith = (state.get("feedback_for_blacksmith") or "").strip()
    feedback_for_searcher = (state.get("feedback_for_searcher") or "").strip()
    qa_attempts = state.get("qa_attempts", 0)
    execution_attempts = state.get("execution_attempts", 0)

    # ── [4] QA cycle protection ───────────────────────────────────────────────
    if qa_attempts >= _MAX_QA_ATTEMPTS and not qa_passed:
        print(f"[Core] QA loop limit reached ({qa_attempts} attempts).")
        return {
            **_common,
            "next_node": "end",
            "step_count": step_count + 1,
            "needs_human_help": True,
            "clarifier_message": (
                f"⚠️ QA не може схвалити агента після {_MAX_QA_ATTEMPTS} спроб.\n"
                f"Помилки: {errors}"
            ),
            "core_memory": core_memory + ["QA loop limit reached. Waiting for human."],
        }

    # ── [5] All sub-agents complete ───────────────────────────────────────────
    if sub_agent_plan and current_index >= len(sub_agent_plan):
        print("[Core] ✅ All sub-agents completed successfully!")
        from tools.notifier import notify_telegram
        import re
        docx_files = []
        for v in sub_agent_results.values():
            if isinstance(v, str):
                match = re.search(r'(?:workspace[/\\][^\s]+\.docx)', v)
                if match:
                    docx_files.append(match.group(0))
        notify_telegram(f"✅ Ares AI завершив задачу!\n{state.get('original_task', '')[:100]}", docx_files[0] if docx_files else None)
        return {
            **_common,
            "next_node": "end",
            "step_count": step_count + 1,
            "completed": True,
            "final_result": json.dumps(sub_agent_results, indent=2, ensure_ascii=False),
            "core_memory": core_memory + ["✅ All sub-agents completed. Task done!"],
        }

    # ── [5b] Plan waiting for human approval ──────────────────────────────────
    if state.get("plan_needs_approval") and not state.get("plan_approved"):
        return {
            **_common,
            "next_node": "end",
            "step_count": step_count + 1,
            "core_memory": core_memory + ["Waiting for human to approve the plan."],
        }

    # ── [6] Asset gap detection ───────────────────────────────────────────────
    missing_image_queries = state.get("missing_image_queries") or manifest_summary["missing_queries"]
    if missing_image_queries:
        searcher_loops = sum(1 for msg in core_memory if "Asset gap detected" in msg)
        if searcher_loops >= _MAX_SEARCHER_LOOPS:
            # Loop limit hit — check if we at least have research data.
            # If yes: proceed to Planner with whatever assets we have (images are optional).
            # Missing images cause a skip in Blacksmith/Executor, not a crash.
            # Only block if we have ZERO research data at all.
            research_exists = (
                bool((state.get("gathered_info") or "").strip())
                or Path(state["context_data_path"]).exists()
            )
            if research_exists:
                print(f"[Core] Image search limit reached. Proceeding to Planner with available assets.")
                return {
                    **_common,
                    "next_node": "planner",
                    "step_count": step_count + 1,
                    "missing_image_queries": [],   # clear — Blacksmith will skip missing images
                    "core_memory": core_memory + [
                        f"Image search limit reached ({_MAX_SEARCHER_LOOPS}x). "
                        f"Proceeding to Planner with available assets."
                    ],
                }
            # No research at all → ask human
            return {
                **_common,
                "next_node": "end",
                "step_count": step_count + 1,
                "needs_human_help": True,
                "clarifier_message": (
                    f"⚠️ Не можу знайти дані та фото для: {', '.join(missing_image_queries)}. "
                    "Перевірте підключення до мережі або уточніть запит."
                ),
                "core_memory": core_memory + ["Searcher loop limit + no research data. Waiting for human."],
            }
        return {
            **_common,
            "next_node": "searcher",
            "step_count": step_count + 1,
            "core_memory": core_memory + ["Asset gap detected; routing back to searcher."],
        }

    # ── [7] Active Multi-Agent Pipeline execution ─────────────────────────────
    if sub_agent_plan and current_index < len(sub_agent_plan):
        current_task = sub_agent_plan[current_index]
        task_id = current_task.get("task_id", f"task_{current_index}")
        task_desc = current_task.get("description", "")

        # [7pre] skill_config готовий — одразу до Executor (незалежно від current_profile)
        if state.get("skill_config") is not None and qa_passed:
            return {
                **_common,
                "next_node": "executor",
                "sub_task": task_desc,
                "step_count": step_count + 1,
                "qa_passed": True,
                "core_memory": core_memory + [f"Skill config ready for '{task_id}'; routing to Executor."],
            }

        # [7a] QA failed → Blacksmith revision
        if errors and feedback_for_blacksmith:
            return {
                **_common,
                "next_node": "blacksmith",
                "sub_task": task_desc,
                "step_count": step_count + 1,
                "core_memory": core_memory + [f"QA failed for '{task_id}'; Blacksmith revising."],
            }

        # [7b] Has profile, QA not yet passed → QA
        if current_profile and not qa_passed:
            # Skip QA if skill_config is set (Blacksmith already validated)
            if state.get("skill_config") is not None:
                return {
                    **_common,
                    "next_node": "executor",
                    "sub_task": task_desc,
                    "step_count": step_count + 1,
                    "qa_passed": True,
                    "core_memory": core_memory + [f"Skill config ready for '{task_id}'; QA skipped, routing to executor."],
                }
            return {
                **_common,
                "next_node": "qa",
                "sub_task": task_desc,
                "step_count": step_count + 1,
                "core_memory": core_memory + [f"Profile ready for '{task_id}'; routing to QA."],
            }

        # [7c] QA passed, execution error → retry executor (max 3 times)
        if current_profile and qa_passed and last_execution_error and execution_attempts < 3:
            return {
                **_common,
                "next_node": "executor",
                "sub_task": task_desc,
                "step_count": step_count + 1,
                "core_memory": core_memory + [f"Retrying executor for '{task_id}' (attempt {execution_attempts + 1})."],
            }

        # [7d] QA passed, no error → execute
        if current_profile and qa_passed and not last_execution_error:
            return {
                **_common,
                "next_node": "executor",
                "sub_task": task_desc,
                "step_count": step_count + 1,
                "core_memory": core_memory + [f"QA passed for '{task_id}'; routing to executor."],
            }

        # [7e] Safety: Blacksmith set feedback_for_searcher → route there, never loop
        # This guards against any future case where data_is_sufficient=False leaks
        # into the pipeline context (deterministic protection regardless of LLM output).
        if feedback_for_searcher and not current_profile:
            print(f"[Core] Blacksmith requested research inside pipeline for '{task_id}'. Routing to searcher.")
            return {
                **_common,
                "next_node": "searcher",
                "step_count": step_count + 1,
                "feedback_for_searcher": feedback_for_searcher,
                "core_memory": core_memory + [f"Blacksmith needs more data for '{task_id}'; routing to searcher."],
            }

        # [7f] No profile yet → create with Blacksmith
        return {
            **_common,
            "next_node": "blacksmith",
            "sub_task": task_desc,
            "step_count": step_count + 1,
            "current_agent_profile": {},
            "skill_config": None,
            "qa_passed": False,
            "errors": [],
            "last_execution_error": "",
            "feedback_for_blacksmith": "",
            "qa_attempts": 0,
            "execution_attempts": 0,
            "core_memory": core_memory + [f"[Sub-Agent {current_index + 1}/{len(sub_agent_plan)}] Creating specialist for '{task_id}'."],
        }

    # ── [8] No plan yet — research and planning ───────────────────────────────
    if feedback_for_searcher:
        return {
            **_common,
            "next_node": "searcher",
            "step_count": step_count + 1,
            "core_memory": core_memory + ["Routing to searcher (Blacksmith requested more research)."],
        }

    research_ready = (
        bool((state.get("gathered_info") or "").strip())
        or Path(state["context_data_path"]).exists()
    )

    if research_ready:
        return {
            **_common,
            "next_node": "planner",
            "step_count": step_count + 1,
            "core_memory": core_memory + ["Research complete; routing to Planner for task decomposition."],
        }

    return {
        **_common,
        "next_node": "searcher",
        "step_count": step_count + 1,
        "core_memory": core_memory + ["Research required; routing to Searcher."],
    }


def core_supervisor(state: AresState) -> dict:
    result = _core_supervisor_internal(state)
    if result.get("next_node") == "searcher":
        search_cycles = state.get("search_cycles", 0)
        if search_cycles >= 2:
            critical = False
            context_path = state.get("context_data_path")
            if context_path and Path(context_path).exists():
                try:
                    with open(context_path, "r", encoding="utf-8") as f:
                        cd = json.load(f)
                        if cd.get("data_quality", {}).get("critical"):
                            critical = True
                except Exception:
                    pass
            
            if critical:
                print("[Core] Max search cycles reached, but data quality is critical. Stopping.")
                result["next_node"] = "end"
                result["needs_human_help"] = True
                result["clarifier_message"] = "⚠️ Не зміг знайти всі необхідні дані (critical=true). Уточніть запит або додайте інформацію."
                if "core_memory" in result:
                    result["core_memory"].append("Max search cycles reached with critical data quality. Waiting for human.")
            else:
                print("[Core] Max search cycles reached. Forcing route to planner.")
                result["next_node"] = "planner"
                result["search_cycles"] = 0
                if "core_memory" in result:
                    result["core_memory"].append("Max search cycles reached. Forced route to planner.")
        else:
            result["search_cycles"] = search_cycles + 1
    return result
