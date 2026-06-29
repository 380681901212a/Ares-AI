"""
SkillRegistry — AresAI 3.0

Central loader for the pre-verified skill modules.
Skills are pure Python functions that execute tasks reliably, without LLM code generation.

Usage:
    from tools.skill_registry import skill_exists, execute_skill, get_registry_summary

    if skill_exists("word_creator"):
        result = execute_skill("word_creator", config, workdir)
"""

import importlib.util
import json
from pathlib import Path

_REGISTRY_PATH = Path(__file__).parent / "__registry__.json"
_SKILLS_DIR    = Path(__file__).parent / "skills"


def load_registry() -> dict:
    """Load the full skill registry from JSON."""
    with open(_REGISTRY_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def skill_exists(skill_id: str) -> bool:
    """Return True if the skill is registered AND its module file exists."""
    registry = load_registry()
    if skill_id not in registry:
        return False
    module_path = _SKILLS_DIR / f"{skill_id}.py"
    return module_path.exists()


def get_skill_info(skill_id: str) -> dict | None:
    """Return skill metadata dict or None."""
    return load_registry().get(skill_id)


def get_skill_for_task_type(task_type: str) -> str | None:
    """Return the first skill_id that matches the given task_type, or None."""
    for skill_id, info in load_registry().items():
        if task_type in info.get("task_types", []):
            module_path = _SKILLS_DIR / f"{skill_id}.py"
            if module_path.exists():
                return skill_id
    return None


def get_registry_summary() -> str:
    """Return a concise human-readable summary of all available skills for prompts."""
    lines = ["Available Skills in SkillRegistry:"]
    for skill_id, info in load_registry().items():
        module_path = _SKILLS_DIR / f"{skill_id}.py"
        status = "✅" if module_path.exists() else "❌ missing"
        task_types = ", ".join(info.get("task_types", []))
        lines.append(
            f"  - {skill_id} {status}: {info.get('description', '')} "
            f"[task_types: {task_types}]"
        )
    return "\n".join(lines)


def execute_skill(skill_id: str, config: dict, workdir: str = ".") -> str:
    """
    Dynamically load and execute a skill module.

    Args:
        skill_id: Key in __registry__.json (e.g. "word_creator")
        config:   Dict of parameters matching the skill's input_schema
        workdir:  Absolute path to the run's working directory (contains workspace/)

    Returns:
        str: Output message (typically a file path). Starts with "Error:" on failure.
    """
    if not skill_exists(skill_id):
        return f"Error: Skill '{skill_id}' not found in registry or module missing."

    module_path = _SKILLS_DIR / f"{skill_id}.py"
    try:
        spec   = importlib.util.spec_from_file_location(skill_id, str(module_path))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.execute(config, workdir)
    except Exception as exc:
        import traceback
        return f"Error: Skill '{skill_id}' execution failed.\n{traceback.format_exc()}"
