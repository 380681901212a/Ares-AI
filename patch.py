import sys

with open('agents/searcher.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Change 1
content = content.replace(
    "{feedback_section}\"\n    )",
    "{budget_guard}{feedback_section}\"\n    )"
)

# Change 2
old_invoke = """        feedback_text = (
            f"\\n\\nFEEDBACK LOOP: The previous search failed or was blocked. "
            f"Feedback: {feedback}. You MUST read this feedback and completely "
            f"change your search strategy or target different websites."
        ) if feedback else ""

        try:
            print(f"🔎 [Searcher] LLM Iteration {iteration + 1}/{max_iterations} - Analyzing search needs...")
            result = chain.invoke({
                "task": current_task,
                "info": aggregated_info if aggregated_info else "None yet.",
                "iteration_num": str(iteration + 1),
                "feedback_section": feedback_text,
                "used_queries": str(list(seen_queries))[:500],
                "evidence_profile": summarize_profile(evidence_profile),
            })"""

new_invoke = """        feedback_text = (
            f"\\n\\nFEEDBACK LOOP: The previous search failed or was blocked. "
            f"Feedback: {feedback}. You MUST read this feedback and completely "
            f"change your search strategy or target different websites."
        ) if feedback else ""

        budget_guard = ""
        if evidence_profile.budget_max:
            budget_guard = f"\\n\\nCRITICAL BUDGET GUARD: The user specified a maximum budget of {evidence_profile.budget_max}. You MUST NOT drift from this budget. Do NOT search for lower limits or different budgets unless explicitly asked."

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
            })"""
content = content.replace(old_invoke, new_invoke)

# Change 3
old_images = """        image_queries = getattr(result, "image_queries", [])
        image_settings = state.get("image_settings", {})
        if not image_settings.get("web_images_enabled", True):
            image_queries = []

        if queries:
            print(f"🔎 [Searcher] Searching web for {len(queries)} text queries...")
            search_results = deep_research(queries)
            aggregated_info += f"\\n\\n--- Iteration {iteration + 1} Web Results ---\\n" + search_results"""

new_images = """        image_queries = getattr(result, "image_queries", [])
        
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
                        search_results += f"\\n\\n--- Extracted Products from Links ---\\n{extracted_offers_text}"
                except Exception as e:
                    print(f"🔎 [Searcher] Offer extraction failed: {e}")
                    
            aggregated_info += f"\\n\\n--- Iteration {iteration + 1} Web Results ---\\n" + search_results"""
content = content.replace(old_images, new_images)

with open('agents/searcher.py', 'w', encoding='utf-8') as f:
    f.write(content)
