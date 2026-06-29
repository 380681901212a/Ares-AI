from llm_config import get_text_llm
from schemas import RequirementsSchema, OutputRequirementsSchema
from state import AresState
from langchain_core.prompts import ChatPromptTemplate

def requirements_agent(state: AresState) -> dict:
    """
    Acts as the Requirements Analyst.
    Перевіряє чи задача достатньо конкретна перед запуском пошуку.

    ВАЖЛИВО: Logic Part 1 (обробка human_response) видалена.
    Core тепер сам оновлює original_task при отриманні відповіді від юзера
    через human_updates в core_supervisor. Дублювання тут викликало подвійне
    дописування [User Clarification] до задачі.
    """
    llm = get_text_llm()
    structured_llm = llm.with_structured_output(RequirementsSchema)

    prompt = ChatPromptTemplate.from_messages([
        ("system",
         "You are the Requirements Analyst. Review the Original Task.\n\n"
         "CRITICAL ENTITY CHECK: Do not just evaluate the length or formatting details "
         "of the task. You MUST actively look for missing CRITICAL ENTITIES.\n"
         "Rule 1: If the task is about a broad brand or category (e.g., 'an Audi car', "
         "'a smartphone', 'a laptop') but the SPECIFIC MODEL (e.g., 'Q7', 'iPhone 15', "
         "'MacBook Pro') is missing, you MUST set needs_clarification to True and ask "
         "for the model.\n"
         "Rule 2: If the task requires specific data (like weather or news) but the "
         "LOCATION or TOPIC is missing, you MUST set needs_clarification to True.\n"
         "IMPORTANT: If the task already contains a '[User Clarification]' block that "
         "answers the missing entity, set needs_clarification to False.\n"
         "Never assume a default model or location. If a core entity is missing, halt and ask."),
        ("user", "Original Task: {task}\nAnalyze if clarification is needed.")
    ])

    chain = prompt | structured_llm

    try:
        result = chain.invoke({"task": state["original_task"]})
    except Exception as e:
        print(f"[Requirements] LLM error: {e}")
        return {"needs_human_help": False}

    if getattr(result, "needs_clarification", False):
        return {
            "needs_human_help": True,
            "clarifier_message": result.question_for_user,
            "paused_by": "requirements",
        }

    # Step 2: Determine exact output format and requirements
    req_llm = llm.with_structured_output(OutputRequirementsSchema)
    req_prompt = ChatPromptTemplate.from_messages([
        ("system", "Analyze the task to determine exact output format requirements. Detect the language from the task text.\nCRITICAL RULE: You MUST default format to 'markdown_ui' so the result is rendered in the UI with tables and formatting. ONLY select 'word_report' if the user explicitly typed 'Word', 'Ворд', or 'docx' in the task."),
        ("user", "Task: {task}")
    ])
    req_chain = req_prompt | req_llm
    
    try:
        req_result = req_chain.invoke({"task": state["original_task"]})
        output_reqs = req_result.model_dump()
        out_lang = req_result.language
    except Exception as e:
        print(f"[Requirements] Format LLM error: {e}")
        output_reqs = {}
        out_lang = "English"

    return {
        "needs_human_help": False, 
        "paused_by": "", 
        "output_requirements": output_reqs, 
        "output_language": out_lang
    }

