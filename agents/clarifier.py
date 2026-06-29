from llm_config import get_llm
from schemas import ClarifierOutputSchema
from state import AresState
from langchain_core.prompts import ChatPromptTemplate

def clarifier_agent(state: AresState) -> dict:
    """
    Acts as the Senior Analyst to ask strategic questions to the user when necessary.
    """
    llm = get_llm()
    structured_llm = llm.with_structured_output(ClarifierOutputSchema)
    
    prompt = ChatPromptTemplate.from_messages([
        ("system", """You are the Senior Analyst. Your ONLY job is to detect ONE of these 3 blockers:
1. A CRITICAL ENTITY is missing from the Task (e.g., brand specified but NO model: "an Audi car" without "Q7").
2. A STRATEGIC technical decision requires user input (e.g., "use paid API or free scraping?").
3. CONFIDENTIAL data is required that cannot be guessed (e.g., API key, password).

ABSOLUTE RULES — follow these BEFORE making any decision:
- If the Task already contains a model name, year, and topic → NEVER set needs_human_help=True.
- NEVER ask about data quality, completeness, or whether specs/prices/images were found. That is NOT your concern.
- NEVER ask the user to "provide more details" about a topic that is already clearly specified.
- If the Task says "[User Clarification]" → the user already answered; set needs_human_help=False.
- If unsure → default to needs_human_help=False and let the pipeline proceed.

EXAMPLES: 
  Task "Audi Q7 2022 Word report with specs" → needs_human_help=False (all entities present)
  Task "Create a report about an Audi" → needs_human_help=True (model missing)
  Task "Use the API key to fetch data" → needs_human_help=True (credential missing)

Output ONLY valid JSON matching the schema."""),
        ("user", "Task: {task}\nGathered Info: {info}\nHuman Response: {human_response}\nAnalyze and determine if human help is needed.")
    ])
    
    chain = prompt | structured_llm

    try:
        result = chain.invoke({
            "task": state["original_task"],
            "info": state.get("gathered_info", "No additional context was gathered."),
            "human_response": state.get("human_response", "")
        })
    except Exception as e:
        print(f"[Clarifier] LLM error: {e}")
        return {"needs_human_help": False, "clarifier_message": "", "resolved_context": ""}

    return {
        "needs_human_help": result.needs_human_help,
        "clarifier_message": result.message_to_user,
        "resolved_context": result.resolved_context,
        # Передаємо sub_task для Blacksmith в Core.
        # Якщо Clarifier не перезаписує sub_task — Blacksmith отримає нерелевантну
        # інструкцію від Searcher замість своєї. sub_task оновлюється Core-ом
        # на наступному кроці, тому ми зберігаємо її незмінною поки Core не вирішить інакше.
        "sub_task": state.get("sub_task", ""),
    }