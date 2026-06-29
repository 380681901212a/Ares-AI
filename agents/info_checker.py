import json
import os

from langchain_core.prompts import ChatPromptTemplate

from llm_config import get_text_llm, reset_llm
from schemas import InfoFilterSchema
from state import AresState
from tools.evidence_profile import build_evidence_profile, summarize_profile, validate_output_against_profile
from tools.source_memory import reward_sources_from_text


def info_filter_agent(state: AresState) -> dict:
    """
    Acts as the Information Sanitizer to filter and summarize gathered information.
    """
    gathered_info = (state.get("gathered_info") or "").strip()
    data_file_path = state["context_data_path"]

    has_web_results = "Source:" in gathered_info or "Web Results" in gathered_info
    if not gathered_info or not has_web_results:
        return {
            "gathered_info": (
                "No new web research was gathered. Use the existing context file if it is already present."
            )
        }

    llm = get_text_llm()
    structured_llm = llm.with_structured_output(InfoFilterSchema)
    evidence_profile = build_evidence_profile(
        state.get("original_task", ""),
        state.get("output_requirements", {}),
    )

    prompt = ChatPromptTemplate.from_messages([
        (
            "system",
            "You are the Information Sanitizer and Data Engineer. Extract and structure all gathered information to fulfill the original task.\n"
            "TASK: {task}\n\n"
            "EVIDENCE PROFILE: {evidence_profile}\n\n"
            "OUTPUT LANGUAGE RULE: Write ALL section titles and content in {language}. "
            "If language is Ukrainian — write in Ukrainian. If English — in English. "
            "Do NOT mix languages.\n\n"
            "OUTPUT REQUIREMENTS: {output_requirements}\n" \
            "If must_have_table is true — MANDATORY populate structured_data with comparison table data.\n" \
            "If must_have_essay is true — write long detailed paragraphs in sections.\n\n" \
            "PRICE/OFFER RULE: If the task asks for prices, offers, budget, or buying options, "
            "structured_data MUST include 'top_offers' as a list of objects with: product, seller, "
            "price, currency, url, why_selected, freshness_note. Also include source links in text.\n"
            "If a budget is present, choose up to 5 best matching offers within or near that budget, "
            "ranked by value, relevance, availability, and source trust.\n"
            "COMPARISON TABLE RULE: If the task asks for comparison/table, structured_data MUST include "
            "'comparison_table' with keys 'headers' and 'rows'.\n"
            "ENTITY STRICTNESS RULE: Do not substitute nearby variants. If the task says iPhone 16, "
            "do not summarize iPhone 16 Pro Max as if it were iPhone 16. If data is missing, say what is missing.\n"
            "CRITICAL INSTRUCTION: Extract data for ALL entities mentioned in the task "
            "(e.g. for a comparison task — cover BOTH subjects thoroughly). "
            "Be specific — include real values, prices, specs. NEVER produce empty fields. "
            "Output ONLY valid JSON matching the schema.",
        ),
        ("user", "Analyze and summarize this information: {info}"),
    ])

    chain = prompt | structured_llm
    try:
        result = chain.invoke({
            "info": gathered_info,
            "task": state.get("original_task", "Extract key facts."),
            "language": state.get("output_language", "English"),
            "output_requirements": __import__("json").dumps(state.get("output_requirements", {})),
            "evidence_profile": summarize_profile(evidence_profile),
        })
    except Exception as exc:
        print(f"[InfoChecker] LLM error: {exc}. Resetting LLM instance.")
        reset_llm()
        fallback_output = {
            "title": "Research extraction failed",
            "description": "The information extraction step failed before structured data could be created.",
            "sections": [],
            "data_quality": {
                "critical": True,
                "missing_fields": ["structured extraction"],
                "entity_issues": [],
                "freshness_issues": [],
                "feedback": f"InfoChecker LLM error: {exc}",
            },
        }
        try:
            os.makedirs(os.path.dirname(data_file_path), exist_ok=True)
            with open(data_file_path, "w", encoding="utf-8") as handle:
                json.dump(fallback_output, handle, indent=4, ensure_ascii=False)
        except Exception as write_exc:
            print(f"[InfoChecker] Could not write fallback context_data: {write_exc}")
        return {
            "gathered_info": (
                "InfoChecker LLM call failed. Using raw gathered_info as fallback. "
                f"Raw info preview: {gathered_info[:500]}"
            ),
            "feedback_for_searcher": (
                "InfoChecker failed to extract structured data. "
                f"Fix extraction or retry with narrower evidence. Error: {exc}"
            ),
        }


    # Build flat universal structure from Option C schema
    output = {
        "title": result.document_title,
        "description": result.executive_summary,
        "sections": [{"title": s.title, "content": s.content} for s in result.sections],
    }
    if result.structured_data:
        output.update(result.structured_data)  # merge pricing, specs etc. at top level

    output["evidence_profile"] = evidence_profile.to_dict()
    output["data_quality"] = validate_output_against_profile(
        output,
        evidence_profile,
        state.get("original_task", ""),
    )
    
    with open(data_file_path, "w", encoding="utf-8") as handle:
        json.dump(output, handle, indent=4, ensure_ascii=False)

    json_preview = json.dumps(output, indent=2, ensure_ascii=False)
    # Зберігаємо короткий витяг (або повний json, оскільки він структурований)
    # щоб наступні агенти бачили, які САМЕ ключі є в базі:
    final_info_message = (
        f"Data successfully extracted and saved to '{data_file_path}'.\n\n"
        f"CRITICAL: The file contains the following JSON structure. You MUST use these exact keys in your Python code:\n"
        f"{json_preview}\n\n"
        "You MUST read this file dynamically in your code to populate the document or results."
    )
    feedback = ""
    if output["data_quality"].get("critical"):
        feedback = (
            "InfoChecker quality gate failed. Search exact missing evidence before planning: "
            + output["data_quality"].get("feedback", "")
        )
    else:
        reward_sources_from_text(
            gathered_info,
            evidence_profile.intents,
            evidence_profile.locale,
        )

    return {"gathered_info": final_info_message, "feedback_for_searcher": feedback}
