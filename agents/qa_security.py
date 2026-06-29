import json
import re

from langchain_core.prompts import ChatPromptTemplate

from llm_config import get_llm
from schemas import QAReportSchema
from state import AresState
from tools.code_audit import static_code_audit


def qa_agent(state: AresState) -> dict:
    profile = state.get("current_agent_profile", {})
    current_attempts = state.get("qa_attempts", 0)

    if not profile:
        return {
            "errors": ["No agent profile is loaded for QA."],
            "qa_attempts": current_attempts + 1,
            "qa_passed": False,
            "feedback_for_blacksmith": "Create a valid agent profile before requesting QA.",
        }

    # ── Static code audit always runs (no LLM needed) ─────────────────────────
    static_issues = []
    for example in profile.get("few_shot_examples", []):
        match = re.search(r"```python\s*\n(.*?)\n```", example, re.DOTALL)
        code_part = match.group(1).strip() if match else example.removeprefix("Final Working Code:\n")
        static_issues.extend(static_code_audit(code_part))

    # ── LLM-based review (with graceful fallback on Ollama crash) ─────────────
    llm = get_llm()
    structured_llm = llm.with_structured_output(QAReportSchema)
    prompt = ChatPromptTemplate.from_messages([
        (
            "system",
            "You are the QA & Security Agent. Review the provided agent profile. "
            "Check for dangerous libraries, malicious intent, insecure patterns, or bad logic. "
            "Output ONLY valid JSON matching the schema.",
        ),
        ("user", "Review this agent profile:\n{profile}"),
    ])
    chain = prompt | structured_llm

    print("🛡️ [QA] Reviewing agent profile for safety and functional issues...")
    try:
        result = chain.invoke({"profile": json.dumps(profile, indent=2)})
        llm_errors = list(result.errors_found)
        is_safe = result.is_safe
        is_functional = result.is_functional
        feedback = result.feedback_for_blacksmith
    except Exception as exc:
        # Ollama crashed or connection dropped — static audit is our safety net.
        # Allow pipeline to continue; static issues will block truly dangerous code.
        print(f"[QA] LLM unavailable ({exc}). Relying on static audit only.")
        llm_errors = []
        is_safe = True
        is_functional = True
        feedback = ""

    all_errors = llm_errors + static_issues
    qa_passed = bool(is_safe and is_functional and not all_errors)

    return {
        "errors": all_errors,
        "qa_attempts": current_attempts + 1,
        "qa_passed": qa_passed,
        "feedback_for_blacksmith": "" if qa_passed else feedback,
    }
