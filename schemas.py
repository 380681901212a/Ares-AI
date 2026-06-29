from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional


# ──────────────────────────────────────────────────────────────────────────────
# PHASE B — NEW: Supervisor schema (Core as orchestrator)
# ──────────────────────────────────────────────────────────────────────────────

class SupervisorSchema(BaseModel):
    reasoning: str = Field(
        description="Why Core made this routing decision. Used for UI debugging and loop detection."
    )
    next_node: str = Field(
        description=(
            "Where to route next. Must be exactly one of: "
            "'requirements', 'searcher', 'blacksmith', 'qa', 'executor', 'end'."
        )
    )
    sub_task: str = Field(
        description="Concrete instruction for the next agent (e.g. 'Search for Audi Q7 front photos')."
    )
    core_memory_update: str = Field(
        description="One-line summary of what was just accomplished (e.g. 'Search complete ✓')."
    )
    max_steps: int = Field(
        description=(
            "Set ONLY on the first call (when core_memory is empty). "
            "Estimate task complexity: simple=8, medium=15, complex=25. "
            "On subsequent calls always return the same value that was already set."
        )
    )


# ──────────────────────────────────────────────────────────────────────────────
# PHASE C — NEW: Executor feedback schema (agent_needs)
# ──────────────────────────────────────────────────────────────────────────────

class ExecutorFeedbackSchema(BaseModel):
    status: str = Field(
        description="'success' if the agent completed its work, 'needs_resources' if it is missing files or data, 'error' if it failed."
    )
    result: str = Field(
        description="Final output message, or description of the error / missing resource."
    )
    agent_needs: str = Field(
        description="If status is 'needs_resources', describe exactly what is missing (e.g. 'Need front photo of Audi Q7 as PNG'). Empty otherwise."
    )
    completed: bool = Field(
        description="True only when the ENTIRE original task is fully finished."
    )

class PlanSchema(BaseModel):
    steps: List[str] = Field(description="The plan steps")
    use_existing_agent: bool = Field(description="True if an existing agent from the registry can perfectly handle the task.")
    selected_agent_name: str = Field(description="The name of the existing agent to use. Empty if use_existing_agent is False.")

class AgentProfileSchema(BaseModel):
    data_is_sufficient: bool = Field(description="True if the context data is good enough to write the code. False if the data is missing, contains Cloudflare errors, or is irrelevant.")
    feedback_for_searcher: str = Field(description="If data_is_sufficient is False, explain exactly what is missing and where the Searcher should look instead. Empty if True.")
    agent_name: str = Field(description="Name of the agent. MUST describe the TECHNOLOGY/TOOL, never the content. e.g. 'WordDocumentSpecialist', not 'AudiQ7WordAgent'.")
    agent_category: str = Field(description="Technology category. One of: 'document', 'image_gen', 'data_analysis', 'web_scraper', 'smm', 'utility'.")
    agent_capabilities: List[str] = Field(description="List of capability tags describing what this agent can do. e.g. ['create word document', 'add tables', 'embed images'].")
    system_prompt: str = Field(description="Highly detailed system prompt for the agent")
    required_libraries: List[str] = Field(description="Libraries required for the agent to function")
    few_shot_examples: List[str] = Field(description="Few shot examples for the agent")

class QAReportSchema(BaseModel):
    is_safe: bool = Field(description="Whether the output is safe")
    is_functional: bool = Field(description="Whether the output is functional")
    errors_found: List[str] = Field(default_factory=list, description="List of errors found, empty if none")
    feedback_for_blacksmith: str = Field(description="Feedback for the blacksmith agent")

class InfoSection(BaseModel):
    title: str = Field(description="Section heading (e.g. 'Technical Specifications', 'Pricing in Ukraine', 'Verdict')")
    content: str = Field(description="Detailed text content for this section. Be thorough and specific — include real values from the gathered data.")


class InfoFilterSchema(BaseModel):
    contains_malicious_content: bool = Field(description="Whether the content is malicious")
    document_title: str = Field(
        description="Full descriptive title for the document (e.g. 'Comparison of Samsung Galaxy S25 and Apple iPhone 16')"
    )
    executive_summary: str = Field(
        description="2-3 sentence executive summary of the topic and key findings from the gathered data"
    )
    sections: List[InfoSection] = Field(
        description=(
            "4-6 thematic sections covering different aspects of the topic. "
            "Each section has 'title' (heading) and 'content' (detailed paragraph text with real data). "
            "Adapt titles to the domain — smartphones: 'Technical Specifications', 'Camera System', "
            "'Battery and Performance', 'Pricing in Ukraine', 'Pros and Cons', 'Verdict'. "
            "Automotive: 'Engine and Performance', 'Interior Features', 'Safety Systems', 'Pricing', 'Verdict'. "
            "NEVER produce empty sections — always extract real data from the gathered information."
        )
    )
    structured_data: dict = Field(
        default_factory=dict,
        description=(
            "Additional domain-specific structured data as a flat dict. "
            "CRITICAL: If the task requires a comparison or table, you MUST include a key exactly named 'comparison_table' "
            "with a valid structure (e.g., {'headers': ['Feature', 'Item 1', 'Item 2'], 'rows': [['Display', 'OLED', 'AMOLED']]}). "
            "Do NOT leave this empty if a table is expected. "
            "Example: {'comparison_table': {'headers': [...], 'rows': [...]}, 'pricing': ...}."
        )
    )

class FinalResultSchema(BaseModel):
    task_completed: bool = Field(description="Whether the overall task was completed successfully")
    final_answer: str = Field(description="The final answer or output of the task")

class SearchQuerySchema(BaseModel):
    query: str = Field(description="A short, optimized English search query to find technical information or APIs needed for the task.")

class SearchEvaluationSchema(BaseModel):
    is_sufficient: bool = Field(description="True ONLY if the gathered info fully and completely answers the original task. Must be False if critical details are missing.")
    queries: list[str] = Field(description="A list of up to 3 NEW search queries to fill the knowledge gaps. Empty if is_sufficient is True.")
    image_queries: list[str] = Field(default_factory=list, description="A list of natural language Google search phrases for finding required images. Example: ['2022 Audi Q7 real photo', '2022 BMW X5 vs Audi Q7 comparison']. Empty if no images needed.")


class OutputRequirementsSchema(BaseModel):
    format: str = Field(
        description="Primary output format: 'markdown_ui' | 'word_report' | 'csv_data' | 'presentation' | 'code'. 'markdown_ui' is the default for rendering rich text and tables directly in the chat UI."
    )
    must_have_table: bool = Field(
        description="True if the task EXPLICITLY mentions a table, comparison, or structured data"
    )
    must_have_essay: bool = Field(
        description="True if the task asks for an essay, article, or long-form text"
    )
    must_have_images: bool = Field(
        description="True if the task mentions photos, images, visual examples"
    )
    must_have_conclusion: bool = Field(
        description="True if the task asks for a verdict, conclusion, or recommendation"
    )
    language: str = Field(
        description="Output language detected from the task text: 'Ukrainian' | 'English' | 'Russian'"
    )
    additional_notes: str = Field(
        default="",
        description="Any other specific format requirements mentioned in the task"
    )

class RequirementsSchema(BaseModel):
    needs_clarification: bool = Field(description="True if the task is too broad or missing critical specific entities (like a specific car model, city, or API name).")
    question_for_user: str = Field(description="The specific question to ask the user to narrow down the scope. Empty if needs_clarification is False.")


class CodeGenerationSchema(BaseModel):
    thought_process: str = Field(description="If this is the first attempt, briefly explain your logic. If you are fixing an error, you MUST deeply analyze the traceback, explain exactly WHY the previous code failed, and state your plan to fix it.")
    code: str = Field(description="The executable Python code. You MUST wrap the code in markdown backticks (e.g., ```python\n<code here>\n```). You are free to use double quotes and any valid Python syntax.")

class ClarifierOutputSchema(BaseModel):
    needs_human_help: bool = Field(description="True if you must ask the user a strategic question, False if you can proceed.")
    message_to_user: str = Field(description="The question for the user. Empty if needs_human_help is False.")
    resolved_context: str = Field(description="If the user provided an answer, summarize the final decision here. Otherwise, leave empty.")


# ──────────────────────────────────────────────────────────────────────────────
# ARES AI 3.0 — SkillRegistry schemas
# ──────────────────────────────────────────────────────────────────────────────

class SkillConfigSchema(BaseModel):
    """Generated by Blacksmith when a matching skill exists in SkillRegistry."""
    skill_id: str = Field(
        description="The skill identifier from SkillRegistry (e.g. 'word_creator', 'image_generator')."
    )
    config: Dict[str, Any] = Field(
        description=(
            "Parameters dict matching the skill's input_schema. "
            "For word_creator: must include 'title', 'output_filename', and 'images' list. "
            "For image_generator: must include 'prompts' list with {prompt, filename}. "
            "For data_processor: must include 'context_data_path'. "
            "For chart_creator: must include 'charts' list and 'context_data_path'."
        )
    )
    reasoning: str = Field(
        description="Brief explanation of the config choices made."
    )


# ──────────────────────────────────────────────────────────────────────────────
# ARES AI 2.0 — Multi-Agent Pipeline schemas
# ──────────────────────────────────────────────────────────────────────────────

class SubAgentTask(BaseModel):
    task_id: str = Field(
        description="Short snake_case identifier for this sub-task (e.g. 'logo_gen', 'data_format', 'word_doc')."
    )
    task_type: str = Field(
        description="Specialist category. Must be exactly one of: 'image_gen', 'data_analysis', 'document', 'utility'."
    )
    description: str = Field(
        description="Detailed instruction for the Blacksmith to create this specialist agent. Include what data to use, what file to produce, and where to save it."
    )
    input_from: List[str] = Field(
        default_factory=list,
        description="List of task_ids whose outputs this task depends on. Empty if no dependencies."
    )
    output_key: str = Field(
        description="Key in sub_agent_results for this task's output (e.g. 'logo_path', 'tables_path', 'document_path', 'result')."
    )
    skill_id: Optional[str] = Field(
        default=None,
        description=(
            "If a pre-verified skill exists in SkillRegistry for this task_type, set this to the skill's ID. "
            "Otherwise leave None. Planner should set this from the registry summary. "
            "Examples: 'word_creator' for document tasks, 'image_generator' for image_gen tasks, "
            "'data_processor' for data_analysis tasks."
        )
    )


class PlannerSchema(BaseModel):
    reasoning: str = Field(
        description="Brief explanation of why this decomposition was chosen."
    )
    is_single_step: bool = Field(
        description="True if the task is simple (fetch data, print result) and needs only ONE specialist agent."
    )
    sub_agent_tasks: List[SubAgentTask] = Field(
        description=(
            "Ordered list of specialist sub-tasks, maximum 4. "
            "For document tasks: typically [image_gen → data_analysis → document]. "
            "For simple tasks: a single utility task."
        )
    )
