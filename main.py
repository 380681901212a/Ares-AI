import copy
import json

from langgraph.graph import END, StateGraph

from agents.asset_inspector import asset_inspector_agent
from agents.blacksmith import blacksmith_agent
from agents.clarifier import clarifier_agent
from agents.core import core_supervisor
from agents.executor import execution_agent
from agents.info_checker import info_filter_agent
from agents.planner import planner_agent
from agents.qa_security import qa_agent
from agents.requirements import requirements_agent
from agents.searcher import deep_research_agent
from state import AresState
from tools.runtime_paths import ensure_run_paths, make_run_id


def supervisor_router(state: AresState) -> str:
    """Reads next_node from the state and returns the next graph node."""
    if state.get("step_count", 0) >= state.get("max_steps", 35):
        return "end"
    next_node = state.get("next_node", "end")
    valid = {"requirements", "searcher", "blacksmith", "qa", "executor", "planner", "end"}
    return next_node if next_node in valid else "end"


def searcher_router(state: AresState) -> str:
    """Routes to asset_inspector if there are unverified assets, otherwise to info_checker."""
    if state.get("asset_requirements"):
        return "asset_inspector"

    from tools.asset_manifest import load_manifest
    manifest = load_manifest(state.get("asset_manifest_path", ""))
    for asset in manifest.get("assets", []):
        if asset.get("status") in ["unverified", "staged"]:
            return "asset_inspector"

    return "info_checker"


# ── Build the AresAI 2.0 StateGraph ──────────────────────────────────────────

# from concurrent.futures import ThreadPoolExecutor, as_completed
# from agents.executor import _get_ready_tasks
# 
# def parallel_executor(state: AresState) -> dict:
#     ready_tasks = _get_ready_tasks(state.get("sub_agent_plan", []), state.get("sub_agent_results", {}))
#     if len(ready_tasks) <= 1:
#         return execution_agent(state)
#         
#     plan = state.get("sub_agent_plan", [])
#     merged_results = dict(state.get("sub_agent_results", {}))
#     last_error = ""
#     completed = False
#     with ThreadPoolExecutor(max_workers=len(ready_tasks)) as pool:
#         futures = []
#         for task in ready_tasks:
#             task_idx = plan.index(task)
#             iso_state = copy.deepcopy(dict(state))
#             iso_state["current_sub_agent_index"] = task_idx
#             iso_state["sub_task"] = task.get("description", "")
#             futures.append(pool.submit(execution_agent, iso_state))
#             
#         for f in as_completed(futures):
#             res = f.result()
#             new_results = res.get("sub_agent_results", {})
#             for k, v in new_results.items():
#                 if k not in merged_results:
#                     merged_results[k] = v
#             if res.get("last_execution_error"):
#                 last_error = res.get("last_execution_error")
#             if res.get("completed"):
#                 completed = True
#                 
#     # Update state with merged results
#     # Index is advanced by one of the workers, we'll let core_supervisor handle it 
#     # Actually core_supervisor relies on current_index advancing.
#     # But parallel_executor replaces executor.
#     # To satisfy core_supervisor, we can just return the merged results
#     return {
#         "sub_agent_results": merged_results,
#         "current_sub_agent_index": state.get("current_sub_agent_index", 0) + len(ready_tasks),
#         "last_execution_error": last_error,
#         "completed": completed
#     }

workflow = StateGraph(AresState)

workflow.add_node("core", core_supervisor)
workflow.add_node("requirements", requirements_agent)
workflow.add_node("searcher", deep_research_agent)
workflow.add_node("asset_inspector", asset_inspector_agent)
workflow.add_node("info_checker", info_filter_agent)
workflow.add_node("clarifier", clarifier_agent)
workflow.add_node("planner", planner_agent)       # NEW in AresAI 2.0
workflow.add_node("blacksmith", blacksmith_agent)
workflow.add_node("qa", qa_agent)
workflow.add_node("executor", execution_agent)

workflow.set_entry_point("core")

workflow.add_conditional_edges(
    "core",
    supervisor_router,
    {
        "requirements": "requirements",
        "searcher": "searcher",
        "planner": "planner",
        "blacksmith": "blacksmith",
        "qa": "qa",
        "executor": "executor",
        "end": END,
    },
)

workflow.add_edge("requirements", "core")
workflow.add_edge("planner", "core")
workflow.add_edge("blacksmith", "core")
workflow.add_edge("qa", "core")
workflow.add_edge("executor", "core")

workflow.add_conditional_edges(
    "searcher",
    searcher_router,
    {
        "asset_inspector": "asset_inspector",
        "info_checker": "info_checker",
    },
)
workflow.add_edge("asset_inspector", "info_checker")
workflow.add_edge("info_checker", "clarifier")
workflow.add_edge("clarifier", "core")

app = workflow.compile()


if __name__ == "__main__":
    import sys
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding='utf-8')

    # ── Quick smoke test — replace with your actual task ──────────────────────
    run_paths = ensure_run_paths(make_run_id())
    initial_state = {
        "original_task": "Write a Python script that prints the current date and time.",
        "qa_attempts": 0,
        "execution_attempts": 0,
        **run_paths,
        "step_count": 0,
        "max_steps": 35,
        "human_response": "",
        "core_memory": [],
        "asset_requirements": [],
        "missing_image_queries": [],
        "accepted_asset_paths": [],
        "qa_passed": False,
        "sub_agent_plan": [],
        "current_sub_agent_index": 0,
        "sub_agent_results": {},
    }

    print("=" * 60)
    print("🚀 Ares AI Ecosystem v2.0 — Smoke Test")
    print(f"Task: {initial_state['original_task']}")
    print("=" * 60)

    result = app.invoke(initial_state)

    print("\n" + "=" * 60)
    print("CORE MEMORY LOG")
    print("=" * 60)
    for entry in result.get("core_memory") or []:
        print(f"  * {entry}")

    print("\n" + "=" * 60)
    print("SUB-AGENT RESULTS")
    print("=" * 60)
    sub_results = result.get("sub_agent_results") or {}
    if sub_results:
        print(json.dumps(sub_results, indent=4, ensure_ascii=False))
    else:
        print("No sub-agent results.")

    print("\n" + "=" * 60)
    print("FINAL RESULT")
    print("=" * 60)
    final_res = result.get("final_result", "")
    print(final_res if final_res else "No execution result.")
