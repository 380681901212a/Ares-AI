from langchain_core.prompts import ChatPromptTemplate
import json
import os
from pathlib import Path

from llm_config import get_llm, reset_llm
from schemas import AgentProfileSchema, SkillConfigSchema
from state import AresState

try:
    from tools.skill_registry import get_skill_info
except Exception:
    def get_skill_info(skill_id): return None

_PATTERNS_DIR = Path(__file__).parent.parent / "registry" / "patterns"

_IMAGE_GEN_EXAMPLE = (
    "```python\n"
    "import torch\n"
    "from diffusers import StableDiffusionPipeline\n"
    "import os\n"
    "\n"
    "def generate_art():\n"
    "    prompt = \"a futuristic cyberpunk city, neon lights, highly detailed, 4k\"\n"
    "    model_id = \"runwayml/stable-diffusion-v1-5\"\n"
    "    pipe = StableDiffusionPipeline.from_pretrained(model_id, torch_dtype=torch.float16)\n"
    "    pipe = pipe.to(\"cuda\")\n"
    "    image = pipe(prompt).images[0]\n"
    "    os.makedirs(\"workspace\", exist_ok=True)\n"
    "    filepath = os.path.join(\"workspace\", \"generated_image.png\")\n"
    "    image.save(filepath)\n"
    "    print(f\"Success: Image generated and saved to {filepath}\")\n"
    "\n"
    "if __name__ == \"__main__\":\n"
    "    generate_art()\n"
    "```"
)

_BASE_SYSTEM_PROMPT = """\
You are the Blacksmith, a Master Creator for the Ares AI Ecosystem v2.0.
Your job is to create a highly specialized agent profile for a SINGLE, focused sub-task.

USER'S ORIGINAL TASK: {original_task}
CURRENT SUB-TASK: {sub_task}
DOCUMENT REQUIREMENTS: {output_requirements}
If must_have_table=True: include a comparison_table section in sections_override if applicable.
IMAGE SETTINGS: {image_settings}
If images_disabled=True, add 'images_disabled': True to the skill config.
TASK TYPE: {task_type}
DOCUMENT REQUIREMENTS: {output_requirements}
If must_have_table=True: include a comparison_table section in sections_override if applicable.
If must_have_essay=True: use long body text, minimize tables.
IMAGE SETTINGS: {image_settings}
If local_images_enabled=True, prefer using assets from workspace/local_assets/.
If images_disabled=True, add 'images_disabled': True to the skill config.
For image_gen tasks, always use model='flux-realism' and prompt template: 'Professional studio product photography of [device], isolated white background, sharp focus, commercial grade, 4K, no text overlays'.
PRIOR SUB-AGENT RESULTS (already completed): {prior_results}
USER'S STRATEGIC DECISION: {resolved_context}
QA FEEDBACK TO ADDRESS: {feedback_for_blacksmith}

AGENT NAMING RULE: Name agents by TECHNOLOGY/TOOL, not by content topic.
  Good: 'WordDocumentSpecialist', 'PollinationsLogoGenerator', 'JSONDataFormatter'
  Bad:  'AudiQ7WordAgent', 'CarReportMaker'
Set agent_category to one of: 'document', 'image_gen', 'data_analysis', 'web_scraper', 'smm', 'utility'.
Set agent_capabilities as reusable lowercase tags (e.g. ['create word document', 'embed images']).

CRITICAL DATA RULE: The real task data lives in 'workspace/context_data.json'.
Do NOT hardcode placeholder data. Read that file dynamically when it exists.

WORKSPACE RULE: ALL generated files MUST be saved inside 'workspace/' using RELATIVE paths.
Use os.makedirs('workspace', exist_ok=True) at the start of the script.

PATHS RULE: NEVER use absolute paths like 'C:\\...' or 'D:\\...'.
Always use relative paths: 'workspace/filename.ext'.

IMPORTS RULE: The VERY FIRST lines of generated code MUST be all import statements.
NEVER reference a class or function without importing it first.

AVAILABLE LOCAL ASSETS (use EXACT relative paths starting with 'workspace/'):
{assets}

The structured context file is at workspace/context_data.json.
Context Data Preview (JSON keys and values):
{gathered_info}
"""

_WORD_DOC_RULES = """
WORD DOCUMENT RULES:
- Use python-docx. Create a highly professional, well-formatted document.
- Include rich multi-paragraph text synthesizing the data, not just tables.
- Apply styles: 'Heading 1', 'Heading 2', 'Heading 3', 'List Bullet'.
- Tables: use native grid style (e.g. table.style = 'Medium Grid 1 Accent 1').
- IMAGE NORMALIZATION: Before add_picture, normalize with Pillow to prevent errors:
    from PIL import Image
    img = Image.open('workspace/photo.jpg').convert('RGB')
    img.save('workspace/temp_photo.jpg', 'JPEG')
    doc.add_picture('workspace/temp_photo.jpg', width=Inches(6))
- Align images: from docx.enum.text import WD_ALIGN_PARAGRAPH
  doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
- NEVER use placeholders like '(Data)' or '(BMW data)'. Use REAL extracted values.
- CRITICAL IMPORTS: Always include `from docx.enum.text import WD_ALIGN_PARAGRAPH`.
- IMAGE ERROR HANDLING: Wrap every doc.add_picture() in try...except to skip bad files gracefully.
"""

_IMAGE_GEN_RULES = """
IMAGE GENERATION RULES:
- For AI-generated logos: use Pollinations API (free, no auth needed):
  import urllib.request
  url = 'https://image.pollinations.ai/prompt/professional%20Audi%20logo%2C%20minimalist'
  urllib.request.urlretrieve(url, 'workspace/logo.jpg')
  print('Success: Logo saved to workspace/logo.jpg')
- For realistic photos: use Stable Diffusion via diffusers library (CUDA required).
- NEVER use PIL/Pillow ImageDraw for realistic images — only for simple shapes/text.
- Always wrap network requests in try...except and print a warning if download fails.
"""

_PRIOR_RESULTS_GUIDANCE = """
PRIOR RESULTS GUIDANCE:
The prior sub-agent results listed above contain file paths produced by earlier agents.
When building your script, use those EXACT paths as inputs. For example, if prior_results
contains {{"logo_gen": "Success:\\nworkspace/audi_logo.jpg"}}, extract the path
'workspace/audi_logo.jpg' and use it directly in doc.add_picture() or similar.
"""


def _load_patterns(task_type: str) -> list[str]:
    """Load up to 2 saved successful patterns for this task_type as few-shot examples."""
    pattern_file = _PATTERNS_DIR / f"{task_type}.json"
    if not pattern_file.exists():
        return []
    try:
        with open(pattern_file, "r", encoding="utf-8") as f:
            patterns = json.load(f)
        # Return last 2 most successful patterns
        sorted_p = sorted(patterns, key=lambda x: x.get("success_count", 0), reverse=True)
        return [f"```python\n{p['code']}\n```" for p in sorted_p[:2] if p.get("code")]
    except Exception:
        return []


_SKILL_CONFIG_PROMPT = """\
You are the Blacksmith in Ares AI 3.0. Your job is to configure a pre-verified skill module.

The skill '{skill_id}' is available in SkillRegistry.
Skill input schema:
{skill_schema}

ORIGINAL TASK: {original_task}
CURRENT SUB-TASK: {sub_task}
DOCUMENT REQUIREMENTS: {output_requirements}
If must_have_table=True: include a comparison_table section in sections_override if applicable.
IMAGE SETTINGS: {image_settings}
If images_disabled=True, add 'images_disabled': True to the skill config.
AVAILABLE LOCAL ASSETS (images/files already downloaded):
{assets}
CONTEXT DATA PREVIEW:
{gathered_info}
PRIOR SUB-AGENT RESULTS:
{prior_results}

Generate a config dict that maps the task requirements to the skill's input_schema.
For word_creator:
  - title: the document title
  - output_filename: e.g. 'Report_Audi_Q7.docx'
  - context_data_path: 'workspace/context_data.json'
  - images: list of {{"path": "workspace/filename.jpg", "caption": "..."}} from available assets
For image_generator:
  - prompts: list of {{"prompt": "...", "filename": "workspace/name.png"}}
For data_processor:
  - context_data_path: 'workspace/context_data.json'
  - output_path: 'workspace/context_data.json' (overwrite with cleaned version)
For chart_creator:
  - context_data_path: 'workspace/context_data.json'
  - charts: list of {{"title", "chart_type", "data_key", "output_filename"}}

IMAGES RULE: Only include images from the AVAILABLE LOCAL ASSETS list above.
Never invent image paths that are not listed there.

Output ONLY valid JSON matching SkillConfigSchema.\
"""


def _generate_skill_config(
    state: AresState,
    skill_id: str,
    skill_info: dict,
    task_meta: dict,
) -> dict:
    """Generate a SkillConfigSchema for a matched SkillRegistry skill."""
    llm = get_llm()
    skill_llm = llm.with_structured_output(SkillConfigSchema)
    prompt = ChatPromptTemplate.from_messages([("system", _SKILL_CONFIG_PROMPT)])
    chain  = prompt | skill_llm

    # Collect assets
    manifest_assets = state.get("asset_manifest", {}).get("assets", [])
    accepted_details = [
        {
            "path":        f"workspace/{os.path.basename(a['path'])}",
            "query":       a.get("query", ""),
            "description": a.get("vision_description", "") or "",
        }
        for a in manifest_assets
        if a.get("status") == "accepted" and a.get("path")
    ]

    # Context data preview
    ctx_path = state.get("context_data_path", "")
    keys_str = "No context data available."
    if ctx_path and os.path.exists(ctx_path):
        try:
            with open(ctx_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            preview = json.dumps(data, indent=2, ensure_ascii=False)
            keys_str = "Context Data Preview:\n" + preview[:4000]
        except Exception:
            pass

    sub_agent_results = state.get("sub_agent_results") or {}
    print(f"⚒️ [Blacksmith] Generating SkillConfig for '{skill_id}'...")
    try:
        result = chain.invoke({
            "skill_id":     skill_id,
            "skill_schema": json.dumps(skill_info.get("input_schema", {}), indent=2),
            "original_task": state.get("original_task", ""),
            "sub_task":     task_meta.get("description", state.get("original_task", "")),
            "output_requirements": json.dumps(state.get("output_requirements", {})),
            "image_settings": json.dumps(state.get("image_settings", {})),
            "assets":       json.dumps(accepted_details, indent=2),
            "gathered_info": keys_str,
            "prior_results": json.dumps(sub_agent_results, indent=2, ensure_ascii=False) if sub_agent_results else "None.",
        })
    except Exception as exc:
        print(f"[Blacksmith] SkillConfig LLM error: {exc} — falling back to code gen.")
        from llm_config import reset_llm
        reset_llm()
        return {
            "skill_id":    None,
            "skill_config": None,
            "current_agent_profile": {},
            "feedback_for_blacksmith": "",
            "errors": [f"SkillConfig generation failed: {exc}"],
            "qa_passed": False,
        }

    print(f"[Blacksmith] ✅ SkillConfig ready for '{skill_id}'. Skipping QA → direct Executor.")
    return {
        "skill_id":              result.skill_id,
        "skill_config":          result.config,
        "current_agent_profile": {},   # empty → Executor uses skill path
        "feedback_for_searcher": "",
        "feedback_for_blacksmith": "",
        "errors":                [],
        "qa_passed":             True,   # skills don't need QA review
        "last_execution_error":  "",
        "final_result":          "",
    }


def blacksmith_agent(state: AresState) -> dict:
    llm = get_llm()
    structured_llm = llm.with_structured_output(AgentProfileSchema)

    # Determine current sub-task type and skill_id
    sub_agent_plan  = state.get("sub_agent_plan") or []
    current_index   = state.get("current_sub_agent_index") or 0
    current_task_meta = sub_agent_plan[current_index] if current_index < len(sub_agent_plan) else {}
    task_type = current_task_meta.get("task_type", "utility")
    skill_id  = current_task_meta.get("skill_id")  # Set by Planner

    # ── SKILL CONFIG PATH (AresAI 3.0) ──────────────────────────────────────
    if skill_id:
        skill_info = get_skill_info(skill_id)
        if skill_info:
            return _generate_skill_config(
                state, skill_id, skill_info, current_task_meta
            )
        else:
            print(f"[Blacksmith] skill_id='{skill_id}' set but not in registry — falling back to code gen.")

    # ── CODE GENERATION PATH (legacy / fallback) ─────────────────────────────
    system_prompt = _BASE_SYSTEM_PROMPT
    if task_type == "document":
        system_prompt += _WORD_DOC_RULES
    if task_type == "image_gen":
        system_prompt += _IMAGE_GEN_RULES
        system_prompt += f"\nREFERENCE EXAMPLE (Stable Diffusion):\n{_IMAGE_GEN_EXAMPLE}\n"
    if sub_agent_plan and len(sub_agent_plan) > 1:
        system_prompt += _PRIOR_RESULTS_GUIDANCE

    system_prompt += "\nOutput ONLY valid JSON matching AgentProfileSchema."

    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("user", "Create the specialist agent profile for this sub-task."),
    ])

    chain = prompt | structured_llm

    # Collect accepted asset details
    manifest_assets = state.get("asset_manifest", {}).get("assets", [])
    accepted_details = []
    for asset in manifest_assets:
        if asset.get("status") == "accepted":
            asset_path_abs = asset.get("path")
            relative_workspace_path = f"workspace/{os.path.basename(asset_path_abs)}" if asset_path_abs else ""
            accepted_details.append({
                "path": relative_workspace_path,
                "query": asset.get("query"),
                "description": asset.get("vision_description") or "",
            })

    # Context data preview — increased to 6000 chars
    context_data_path = state.get("context_data_path", "")
    keys_str = "No valid JSON structure found in context data."
    if context_data_path and os.path.exists(context_data_path):
        try:
            with open(context_data_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            preview = json.dumps(data, indent=2, ensure_ascii=False)
            keys_str = "Context Data Preview:\n" + preview[:6000]
            if len(preview) > 6000:
                keys_str += "\n... (truncated)"
        except Exception:
            pass

    assets_str = json.dumps(accepted_details, indent=2)
    sub_agent_results = state.get("sub_agent_results") or {}
    current_sub_task = state.get("sub_task", "") or current_task_meta.get("description", "") or state.get("original_task", "")
    original_task = state.get("original_task", "")

    # Inject saved patterns as few-shot examples (Pattern Learning)
    saved_patterns = _load_patterns(task_type)
    if saved_patterns:
        few_shot_str = "\nSUCCESSFUL PATTERNS FROM PREVIOUS RUNS (use these as reference):\n" + "\n---\n".join(saved_patterns)
        system_prompt += few_shot_str

    print("⚒️ [Blacksmith] Forging specialist agent profile (code-gen path)...")
    try:
        result = chain.invoke({
            "original_task": original_task,
            "sub_task": current_sub_task,
            "task_type": task_type,
            "output_requirements": json.dumps(state.get("output_requirements", {})),
            "image_settings": json.dumps(state.get("image_settings", {})),
            "prior_results": json.dumps(sub_agent_results, indent=2, ensure_ascii=False) if sub_agent_results else "None (first sub-agent).",
            "resolved_context": state.get("resolved_context", ""),
            "feedback_for_blacksmith": state.get("feedback_for_blacksmith", ""),
            "assets": assets_str,
            "gathered_info": keys_str,
        })
    except Exception as exc:
        print(f"[Blacksmith] LLM error: {exc}")
        reset_llm()
        return {
            "feedback_for_searcher": "",
            "feedback_for_blacksmith": "",
            "current_agent_profile": {},
            "current_specialist": "",
            "qa_passed": False,
            "final_result": "",
            "errors": [f"Blacksmith LLM error: {exc}"],
        }

    # AresAI 2.0: When inside the multi-agent pipeline (sub_agent_plan exists),
    # data is ALWAYS already gathered by Searcher+InfoChecker before Blacksmith is called.
    # Skip the data_is_sufficient gate — it only applies to the legacy single-step path.
    inside_pipeline = bool(state.get("sub_agent_plan"))

    if not inside_pipeline and not getattr(result, "data_is_sufficient", True):
        print("[Blacksmith] data_is_sufficient=False — requesting more research.")
        return {
            "feedback_for_searcher": result.feedback_for_searcher,
            "feedback_for_blacksmith": "",
            "current_agent_profile": {},
            "current_specialist": "",
            "qa_passed": False,
            "final_result": "",
        }

    return {
        "current_agent_profile": result.model_dump(),
        "current_specialist": result.agent_name,
        "feedback_for_searcher": "",
        "feedback_for_blacksmith": "",
        "errors": [],
        "qa_passed": False,
        "last_execution_error": "",
        "final_result": "",
    }
