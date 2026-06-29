import json
import os
import sys

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')

from state import AresState
from main import app
from tools.runtime_paths import ensure_run_paths, make_run_id

def run_test():
    run_paths = ensure_run_paths(make_run_id())

    # We will trigger the pipeline directly for a Word document task
    initial_state = AresState(
        original_task="Створи презентаційний Word-документ про Audi Q7 2022. Включи інформацію про ціни, технічні характеристики та порівняння з BMW X5. Використай доступні зображення.",
        qa_attempts=0,
        execution_attempts=0,
        **run_paths,
        step_count=0,
        max_steps=10,
        human_response="",
        core_memory=[],
        asset_requirements=[],
        missing_image_queries=[],
        accepted_asset_paths=[],
        qa_passed=False,
        sub_agent_plan=[],
        current_sub_agent_index=0,
        sub_agent_results={},
    )

    print("=" * 60)
    print("🚀 Ares AI Ecosystem v3.0 — End-to-End Test")
    print(f"Task: {initial_state['original_task']}")
    print("=" * 60)

    # Note: Because Ollama LLM is involved, this might take a minute 
    # to run Planner and Blacksmith. But since we use SkillRegistry, 
    # Executor should skip Code Generation!
    try:
        result = app.invoke(initial_state)

        print("\n" + "=" * 60)
        print("CORE MEMORY LOG")
        print("=" * 60)
        for entry in result.get("core_memory", []):
            print(f"  * {entry}")

        print("\n" + "=" * 60)
        print("SUB-AGENT RESULTS")
        print("=" * 60)
        sub_results = result.get("sub_agent_results", {})
        if sub_results:
            print(json.dumps(sub_results, indent=4, ensure_ascii=False))
        else:
            print("No sub-agent results.")

        print("\n" + "=" * 60)
        print("FINAL RESULT")
        print("=" * 60)
        final_res = result.get("final_result", "")
        print(final_res if final_res else "No execution result.")

    except Exception as e:
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    run_test()
