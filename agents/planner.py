"""Planner Agent — AresAI 3.0

Decomposes complex tasks into an ordered Multi-Agent Pipeline.
Now skill-aware: reads SkillRegistry and sets skill_id on each task when a
pre-verified skill module is available — allowing Executor to skip LLM code
generation entirely and run the skill directly.
"""

import json
import os

from langchain_core.prompts import ChatPromptTemplate

from llm_config import get_llm, reset_llm
from schemas import PlannerSchema
from state import AresState
from tools.run_logger import RunLogger

# SkillRegistry summary injected into the prompt
try:
    from tools.skill_registry import get_registry_summary
    _REGISTRY_SUMMARY = get_registry_summary()
except Exception:
    _REGISTRY_SUMMARY = "No skills available."


_SYSTEM_PROMPT = """\
You are the Ares AI Planner — an expert at decomposing complex tasks into an ordered \
pipeline of narrow specialist agents.

ORIGINAL TASK: {task}
USER REQUIRES: {output_requirements}
IMAGE SETTINGS: {image_settings}
CRITICAL: If ai_images_enabled is False, DO NOT include image_gen tasks.
GATHERED RESEARCH SUMMARY: {gathered_info}
AVAILABLE LOCAL ASSETS (accepted images): {accepted_assets}

{registry_summary}

DECOMPOSITION RULES:
1. For SIMPLE tasks (fetch a price, list features, run a calculation) → set is_single_step=True \
and create exactly ONE task with task_type='utility'.
2. For COMPLEX tasks (create Word reports, multi-step data processing with images) → create \
2-4 specialist tasks in logical order.
3. Each task must have ONE clear responsibility. Never overlap.
4. Maximum 4 tasks total.
5. Tasks that need outputs from prior tasks must list those task_ids in input_from.
6. If format is 'markdown_ui', DO NOT create a 'document' task (do not use word_creator). Instead, create a 'utility' task at the end to generate a rich Markdown report (with tables if needed) and return it as the final result.
7. If format is 'word_report', use the 'word_creator' skill and do NOT create a separate utility/table_gen task.
8. If the gathered research/context mentions top_offers or prices, the final output SHOULD include an offers section/table with links and prices, regardless of format.

TASK TYPES AND SKILL MAPPING:
  'image_gen'     — generate/download images. If skill 'image_generator' is available → set skill_id='image_generator'
  'data_analysis' — read context_data.json, extract structured data. If skill 'data_processor' available → set skill_id='data_processor'
  'document'      — create Word/PDF documents. If skill 'word_creator' is available → set skill_id='word_creator'
  'utility'       — general scripts, API calls, computations. Check registry for matching skill.

SKILL_ID RULE:
  If a skill from the SkillRegistry matches this task_type → set skill_id to that skill's name.
  If no matching skill → leave skill_id as null (Blacksmith will generate code instead).

FIELD RULES:
  task_id:      short snake_case identifier (e.g. 'logo_gen', 'data_format', 'word_doc')
  output_key:   what the agent produces (e.g. 'logo_path', 'tables_path', 'document_path')
  description:  detailed instruction for Blacksmith.
                Mention exact output filenames, data sources, and images to use.

EXAMPLE PLAN for "Create a Word report for Audi Q7 2022":
  Task 1: task_id='logo_gen', type='image_gen', skill_id='image_generator'
    → Generate Audi logo via Pollinations API, save as workspace/audi_logo.png
  Task 2: task_id='data_clean', type='data_analysis', skill_id='data_processor'
    → Normalize workspace/context_data.json (remove raw iteration keys)
  Task 3: task_id='word_doc', type='document', skill_id='word_creator', input_from=['logo_gen','data_clean']
    → Build professional Word document combining accepted photos, logo, and JSON data

Output ONLY valid JSON matching PlannerSchema.\
"""


def planner_agent(state: AresState) -> dict:
    """Decomposes the original task into an ordered multi-agent pipeline."""
    llm = get_llm()
    structured_llm = llm.with_structured_output(PlannerSchema)
    prompt = ChatPromptTemplate.from_messages([("system", _SYSTEM_PROMPT)])
    chain  = prompt | structured_llm

    # Build accepted assets summary for context
    manifest_assets = state.get("asset_manifest", {}).get("assets", [])
    accepted_assets = [
        {
            "path":        f"workspace/{os.path.basename(a['path'])}",
            "query":       a.get("query", ""),
            "description": a.get("vision_description", ""),
        }
        for a in manifest_assets
        if a.get("status") == "accepted" and a.get("path")
    ]

    gathered_info = (state.get("gathered_info") or "No research gathered.").strip()

    print("🗺️ [Planner] Decomposing task into specialist sub-agents...")
    try:
        reqs = state.get("output_requirements", {})
        reqs_str = f"format={reqs.get('format')}, table={reqs.get('must_have_table')}, images={reqs.get('must_have_images')}, language={reqs.get('language')}"
        result = chain.invoke({
            "task":             state["original_task"],
            "output_requirements": reqs_str,
            "image_settings":   json.dumps(state.get("image_settings", {})),
            "gathered_info":    gathered_info[:2500],
            "accepted_assets":  json.dumps(accepted_assets, indent=2, ensure_ascii=False),
            "registry_summary": _REGISTRY_SUMMARY,
        })
    except Exception as exc:
        print(f"[Planner] LLM error: {exc}. Falling back to single-step plan.")
        reset_llm()
        return {
            "sub_agent_plan": [
                {
                    "task_id":    "main_task",
                    "task_type":  "utility",
                    "description": state["original_task"],
                    "input_from": [],
                    "output_key": "result",
                    "skill_id":   None,
                }
            ],
            "plan_needs_approval":     True,
            "plan_approved":           False,
            "current_sub_agent_index": 0,
            "sub_agent_results":       {},
            "current_agent_profile":   {},
            "qa_passed":               False,
            "errors":                  [],
            "last_execution_error":    "",
            "feedback_for_blacksmith": "",
            "qa_attempts":             0,
            "execution_attempts":      0,
            "skill_id":                None,
            "skill_config":            None,
        }

    tasks    = [task.model_dump() for task in result.sub_agent_tasks]
    
    # ── Deterministic skill_id mapping (don't rely on LLM to know exact skill names) ──
    _SKILL_MAP = {
        "data_analysis": "data_processor",
        "document":      "word_creator",
        "image_gen":     "image_generator",
        "utility":       None,
        "table_gen":     "data_processor",
        "data_clean":    "data_processor",
    }
    for task in tasks:
        if not task.get("skill_id") and task.get("task_type") in _SKILL_MAP:
            task["skill_id"] = _SKILL_MAP[task["task_type"]]
    document_task_exists = any(task.get("task_type") == "document" for task in tasks)
    dropped_table_tasks = set()
    filtered_tasks = []
    for task in tasks:
        task_text = f"{task.get('task_id', '')} {task.get('description', '')}".lower()
        if document_task_exists and task.get("task_type") == "utility" and "table" in task_text:
            dropped_table_tasks.add(task.get("task_id", ""))
            continue
        filtered_tasks.append(task)
    if dropped_table_tasks:
        for task in filtered_tasks:
            task["input_from"] = [
                dep for dep in task.get("input_from", [])
                if dep not in dropped_table_tasks
            ]
        tasks = filtered_tasks
    RunLogger(state["run_id"]).log("planner", "plan_created", {"tasks": tasks})
    task_ids = [t["task_id"] for t in tasks]
    skill_labels = [
        f"{t['task_id']}({'⚡' + t['skill_id'] if t.get('skill_id') else '🔧code'})"
        for t in tasks
    ]
    print(f"🗺️ [Planner] Pipeline ({len(tasks)} tasks): {' → '.join(skill_labels)}")
    print(f"   Reasoning: {result.reasoning}")

    return {
        "sub_agent_plan":          tasks,
        "plan_needs_approval":     True,
        "plan_approved":           False,
        "current_sub_agent_index": 0,
        "sub_agent_results":       {},
        "current_agent_profile":   {},
        "qa_passed":               False,
        "errors":                  [],
        "last_execution_error":    "",
        "feedback_for_blacksmith": "",
        "qa_attempts":             0,
        "execution_attempts":      0,
        "skill_id":                None,
        "skill_config":            None,
    }
