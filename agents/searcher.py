import os

from langchain_core.prompts import ChatPromptTemplate

from llm_config import get_text_llm
from schemas import SearchEvaluationSchema
from state import AresState
from tools.asset_manifest import load_manifest, normalize_image_requirement, summarize_manifest
from tools.evidence_profile import (
    build_evidence_profile,
    build_seed_queries,
    canonicalize_search_query,
    summarize_profile,
)
from tools.global_asset_index import stage_reusable_assets_for_run
from tools.source_memory import get_preferred_source_domains
from tools.web_search import deep_image_search, deep_research

def deep_research_agent(state: AresState) -> dict:
    """
    Recursively formulates search queries and evaluates gathered information.

    BUG FIX #1: Замінено f-string інтерполяцію в шаблоні на template variables.
    Попередній код вставляв task/aggregated_info напряму в рядок шаблону через
    f-string. Якщо ці значення містили фігурні дужки {} (JSON, Python-код тощо),
    LangChain кидав KeyError або ValueError і весь пайплайн падав.
    """
    aggregated_info = state.get("gathered_info", "") or ""
    iteration = 0
    max_iterations = int(os.getenv("ARES_SEARCH_MAX_ITERATIONS", "6"))
    max_text_queries = int(os.getenv("ARES_SEARCH_MAX_QUERIES_PER_ITERATION", "5"))
    seen_queries: set[str] = set()
    all_image_requirements: list[str] = []
    seen_requirement_keys: set[str] = set()

    current_task = state.get("sub_task", "") or state.get("original_task", "")
    evidence_profile = build_evidence_profile(
        current_task,
        state.get("output_requirements", {}),
    )
    if "top_offers" in evidence_profile.intents:
        max_iterations = min(max_iterations, int(os.getenv("ARES_OFFER_SEARCH_MAX_ITERATIONS", "3")))
    seed_queries = build_seed_queries(evidence_profile)
    preferred_domains = get_preferred_source_domains(
        evidence_profile.intents,
        evidence_profile.locale,
    )
    if preferred_domains and evidence_profile.entities:
        source_seed_queries = []
        for entity in evidence_profile.entities[:2]:
            for domain in preferred_domains[:3]:
                if "local_price" in evidence_profile.intents or "top_offers" in evidence_profile.intents:
                    source_seed_queries.append(f"{entity} {domain} price {evidence_profile.locale}".strip())
                else:
                    source_seed_queries.append(f"{entity} {domain} information".strip())
        seed_queries = source_seed_queries + seed_queries
    if evidence_profile.intents:
        aggregated_info += (
            "\n\n--- Evidence Profile ---\n"
            + summarize_profile(evidence_profile)
        )

    llm = get_text_llm()
    eval_llm = llm.with_structured_output(SearchEvaluationSchema)

    # Шаблон визначається ОДИН РАЗ поза циклом — без f-string.
    # Всі динамічні дані передаються через chain.invoke() як змінні.
    system_template = (
        "You are the Head of Research. Current Sub-Task: {task}. "
        "Information gathered so far: {info}. "
        "Evaluate if the gathered info is sufficient to perfectly complete the task. "
        "Evidence profile for this task: {evidence_profile}. "
        "If prices/offers are requested, every useful result should preserve seller/source links, "
        "currency, locale, and freshness. Do not replace requested models with nearby variants "
        "(for example Ultra/Pro Max/Plus) unless the task explicitly requested those variants. "
        "Keep search queries in the user's task language when practical. If the user wrote Ukrainian, "
        "do not drift into Russian unless the feedback or target source clearly requires it. "
        "NOTE: You MUST perform at least 2 iterations. This is iteration {iteration_num}. "
        "If iteration is 1, is_sufficient MUST be False and you must provide queries. "
        "If you need more info, provide new, highly specific search queries to fill the gaps.\n\n"
        "CRITICAL DIVERSITY RULE: You MUST provide ENTIRELY NEW search queries different from all previous ones. "
        "Queries already used: {used_queries}. "
        "Do NOT repeat any of them. Each iteration must explore new angles: "
        "deeper specs, specific prices, expert reviews, benchmark tests, user forums.\n\n"
        "CRITICAL SEARCH STRATEGY: Generate highly specific, natural language search queries. "
        "Do NOT use advanced search operators like 'site:' or 'intitle:' because they break "
        "our search API. Instead, include keywords like 'official documentation', 'release notes', "
        "'guide', or 'pricing' directly in the natural text of the query.\n\n"
        "CRITICAL IMAGE RULE: If the sub_task asks you to gather REAL PHOTOS or IMAGES, you MUST strictly put those search demands inside the `image_queries` dictionary. DO NOT put photo queries in the regular `queries` list.\n\n"
        "CRITICAL TACTIC: For pure specs/research queries, avoid scraper-hostile shop pages by "
        "favoring informational words like 'official specs', 'wiki', 'forum', or 'guide'. "
        "However, if the evidence profile asks for local_price/top_offers, DO NOT exclude shops: "
        "you must search seller/marketplace pages and preserve offer links.\n\n"
        "CRITICAL TRANSLATION RULE: If the original task uses local jargon or non-English abbreviations "
        "(e.g., 'ТТХ' in Ukrainian/Russian heavily implies 'Technical Specifications'), you MUST mentally "
        "translate these to their correct English equivalents before forming queries. Do NOT search for literal "
        "foreign abbreviations like 'TTX' unless it is explicitly a brand name.{budget_guard}{feedback_section}"
    )
    prompt = ChatPromptTemplate.from_messages([("system", system_template)])
    chain = prompt | eval_llm

    while iteration < max_iterations:
        feedback = state.get("feedback_for_searcher", "")
        feedback_text = (
            f"\n\nFEEDBACK LOOP: The previous search failed or was blocked. "
            f"Feedback: {feedback}. You MUST read this feedback and completely "
            f"change your search strategy or target different websites."
        ) if feedback else ""

        budget_guard = ""
        if evidence_profile.budget_max:
            budget_guard = f"\n\nCRITICAL BUDGET GUARD: The user specified a maximum budget of {evidence_profile.budget_max}. You MUST NOT drift from this budget. Do NOT search for lower limits or different budgets unless explicitly asked."

        try:
            print(f"🔎 [Searcher] LLM Iteration {iteration + 1}/{max_iterations} - Analyzing search needs...")
            result = chain.invoke({
                "task": current_task,
                "info": aggregated_info if aggregated_info else "None yet.",
                "iteration_num": str(iteration + 1),
                "budget_guard": budget_guard,
                "feedback_section": feedback_text,
                "used_queries": str(list(seen_queries))[:500],
                "evidence_profile": summarize_profile(evidence_profile),
            })
        except Exception as e:
            print(f"[Searcher] LLM error on iteration {iteration + 1}: {e}")
            break

        if getattr(result, "is_sufficient", False) and iteration >= 1:
            break

        llm_queries = list(getattr(result, "queries", []) or [])
        queries = (seed_queries if iteration == 0 else []) + llm_queries
        new_queries = []
        for query in queries:
            query = str(query).strip()
            if not query:
                continue
            query_key = canonicalize_search_query(query)
            if query_key in seen_queries:
                continue
            seen_queries.add(query_key)
            new_queries.append(query)
            if len(new_queries) >= max_text_queries:
                break
        queries = new_queries
        image_queries = getattr(result, "image_queries", [])
        
        if "top_offers" in evidence_profile.intents and not state.get("output_requirements", {}).get("must_have_images"):
            image_queries = []
            
        image_settings = state.get("image_settings", {})
        if not image_settings.get("web_images_enabled", True):
            image_queries = []

        if queries:
            print(f"🔎 [Searcher] Searching web for {len(queries)} text queries...")
            search_results = deep_research(queries)
            
            if "top_offers" in evidence_profile.intents:
                try:
                    from tools.offer_extractor import extract_offers_from_search_results
                    extracted_offers_text = extract_offers_from_search_results(search_results, evidence_profile.budget_max)
                    if extracted_offers_text:
                        search_results += f"\n\n--- Extracted Products from Links ---\n{extracted_offers_text}"
                except Exception as e:
                    print(f"🔎 [Searcher] Offer extraction failed: {e}")
                    
            aggregated_info += f"\n\n--- Iteration {iteration + 1} Web Results ---\n" + search_results

        image_queries_dicts = []
        if isinstance(image_queries, list):
            for raw_query in image_queries:
                if isinstance(raw_query, str) and raw_query.strip():
                    clean_query = raw_query.strip()
                    requirement_key = normalize_image_requirement(clean_query)
                    if requirement_key not in seen_requirement_keys:
                        all_image_requirements.append(clean_query)
                        seen_requirement_keys.add(requirement_key)
                    image_queries_dicts.append({"query": clean_query, "save_as": clean_query})
                elif hasattr(raw_query, "model_dump"):
                    payload = raw_query.model_dump()
                    clean_query = str(payload.get("query", "")).strip()
                    if clean_query:
                        requirement_key = normalize_image_requirement(clean_query)
                        if requirement_key not in seen_requirement_keys:
                            all_image_requirements.append(clean_query)
                            seen_requirement_keys.add(requirement_key)
                        image_queries_dicts.append({"query": clean_query, "save_as": clean_query})
                elif isinstance(raw_query, dict):
                    clean_query = str(raw_query.get("query", "")).strip()
                    if clean_query:
                        requirement_key = normalize_image_requirement(clean_query)
                        if requirement_key not in seen_requirement_keys:
                            all_image_requirements.append(clean_query)
                            seen_requirement_keys.add(requirement_key)
                        image_queries_dicts.append({"query": clean_query, "save_as": raw_query.get("save_as", clean_query)})

        if image_queries_dicts:
            manifest = load_manifest(state["asset_manifest_path"])
            reuse_summary = stage_reusable_assets_for_run(
                state["asset_manifest_path"],
                state["run_id"],
                [entry["query"] for entry in image_queries_dicts],
                existing_manifest=manifest,
                index_path=state.get("global_asset_index_path", ""),
            )
            manifest = load_manifest(state["asset_manifest_path"])
            manifest_summary = summarize_manifest(
                manifest,
                [entry["query"] for entry in image_queries_dicts],
            )
            missing_queries = set(manifest_summary["missing_queries"])
            missing_payload = [
                entry for entry in image_queries_dicts
                if entry["query"] in missing_queries
            ]
            if missing_payload:
                print(f"🔎 [Searcher] Sourcing {len(missing_payload)} unique missing images...")
                image_results = deep_image_search(
                    missing_payload,
                    workspace_dir=state["run_workspace_dir"],
                    downloaded_urls_file=state["downloaded_urls_path"],
                    manifest_path=state["asset_manifest_path"],
                    run_id=state["run_id"],
                )
                aggregated_info += (
                    f"\n\n--- Iteration {iteration + 1} Image Results ---\n"
                    + str(image_results["summary"])
                )
                if reuse_summary["reused_count"]:
                    aggregated_info += (
                        f"\nReused {reuse_summary['reused_count']} global asset(s) "
                        "before downloading only the still-missing images."
                    )
            else:
                reuse_note = ""
                if reuse_summary["reused_count"]:
                    reuse_note = (
                        f" Reused {reuse_summary['reused_count']} global asset(s) "
                        "from the cache instead of downloading new ones."
                    )
                aggregated_info += (
                    f"\n\n--- Iteration {iteration + 1} Image Results ---\n"
                    "Existing assets from the current run or global cache already satisfy these image queries."
                    + reuse_note
                )

        if (
            "top_offers" in evidence_profile.intents
            and iteration >= 1
            and aggregated_info.count("Source:") >= 5
        ):
            print("🔎 [Searcher] Offer evidence collected; stopping early to avoid over-searching.")
            break

        iteration += 1

    manifest = load_manifest(state["asset_manifest_path"])
    summary = summarize_manifest(manifest, all_image_requirements)

    return {
        "gathered_info": aggregated_info,
        "asset_requirements": all_image_requirements,
        "asset_manifest": manifest,
        "asset_manifest_summary": summary,
        "missing_image_queries": summary["missing_queries"],
        "feedback_for_searcher": "",
    }
