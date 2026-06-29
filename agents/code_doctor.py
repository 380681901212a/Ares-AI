"""
code_doctor.py — AresAI 3.0

Self-healing module: fixes broken LLM-generated Python code automatically.

Called by the Executor between retry attempts instead of blindly re-submitting
the same prompt to the LLM. The Code Doctor receives the failed code + exact
traceback and performs a targeted surgical fix.
"""

import re

from llm_config import get_llm, reset_llm


def _extract_code(raw: str) -> str:
    match = re.search(r"```python\s*\n(.*?)\n```", raw, re.DOTALL)
    if match:
        return match.group(1).strip()
    # Try without language tag
    match = re.search(r"```\s*\n(.*?)\n```", raw, re.DOTALL)
    if match:
        return match.group(1).strip()
    return raw.strip()


_SYSTEM_PROMPT = """\
You are the Code Doctor — an expert Python debugger.
You receive failing Python code and its exact error traceback.
Your job: fix ONLY the broken line(s) and return the corrected complete script.

RULES:
1. Output ONLY the fixed Python code, wrapped in ```python ... ``` markers.
2. Do NOT add explanations, commentary, or prose outside the code block.
3. Keep all working code exactly as-is. Change only what is broken.
4. If the error is a missing import (NameError, ModuleNotFoundError) — add the import at the top.
5. If the error is a wrong method call (AttributeError) — replace with the correct method.
6. If the error is a file path issue — use relative paths starting with 'workspace/'.
7. If python-docx is involved, use these correct patterns:
   - Tables: table = doc.add_table(rows=N, cols=M); table.style = 'Table Grid'
   - Images: from docx.shared import Inches; doc.add_picture(path, width=Inches(5.5))
   - Normalize images first: from PIL import Image; img=Image.open(p).convert('RGB'); img.save(tmp)
   - Headings: doc.add_heading('text', level=1)
   - Paragraphs: p = doc.add_paragraph(); run = p.add_run('text')
8. Always start with: import os, import json, import sys

FAILED CODE:
{code}

ERROR TRACEBACK:
{error}

Output the corrected Python script:
"""


def fix(failed_code: str, error: str) -> str:
    """
    Attempt to fix broken Python code using the LLM.

    Args:
        failed_code: The Python code that raised an exception
        error:       The full traceback / error message

    Returns:
        Fixed Python code string. Returns the original code if fixing failed.
    """
    if not failed_code.strip():
        return failed_code

    # Quick deterministic fixes first (no LLM needed)
    fixed = _deterministic_fix(failed_code, error)

    # Then LLM-based fix
    try:
        llm = get_llm()
        prompt_text = _SYSTEM_PROMPT.format(
            code=failed_code[:4000],       # Truncate very long scripts
            error=error[:1500],
        )
        response = llm.invoke(prompt_text)
        content  = response.content if hasattr(response, "content") else str(response)
        llm_fixed = _extract_code(content)
        if llm_fixed and len(llm_fixed) > 50:
            print("[Code Doctor] 🩺 LLM fix applied.")
            return _ensure_stdlib_imports(llm_fixed)
    except Exception as exc:
        print(f"[Code Doctor] LLM fix failed: {exc}. Using deterministic fix only.")
        reset_llm()

    return _ensure_stdlib_imports(fixed)


# ── Deterministic fixes (fast, no LLM) ────────────────────────────────────────

def _deterministic_fix(code: str, error: str) -> str:
    """Apply rule-based fixes for common known errors."""
    fixed = code

    # Fix 1: Missing stdlib imports
    fixed = _ensure_stdlib_imports(fixed)

    # Fix 2: Absolute paths → relative paths
    if "No such file or directory" in error and "C:\\" in fixed:
        fixed = re.sub(r"['\"]C:\\[^'\"]*\\workspace\\([^'\"]+)['\"]",
                       r"'workspace/\1'", fixed)

    # Fix 3: Wrong python-docx table style
    if "KeyError" in error and "style" in error:
        fixed = fixed.replace(
            "table.style = 'Medium Grid 1 Accent 1'",
            "table.style = 'Table Grid'"
        )

    # Fix 4: Missing Inches import for add_picture
    if "Inches" in error and "not defined" in error:
        if "from docx.shared import" in fixed:
            fixed = re.sub(
                r"(from docx\.shared import )([^\n]+)",
                lambda m: m.group(0) if "Inches" in m.group(2)
                          else m.group(1) + m.group(2).rstrip() + ", Inches",
                fixed
            )
        elif "from docx" in fixed:
            fixed = "from docx.shared import Inches\n" + fixed

    # Fix 5: Pillow normalize before add_picture
    if "cannot identify image file" in error.lower():
        fixed = _inject_pillow_normalize(fixed)

    return fixed


def _ensure_stdlib_imports(code: str) -> str:
    """Prepend common stdlib imports if not already in the first 10 lines."""
    header_needed = []
    first_lines = "\n".join(code.split("\n")[:10])
    for imp in ("import os", "import json", "import sys", "import re"):
        if imp not in first_lines:
            header_needed.append(imp)
    if header_needed:
        return "\n".join(header_needed) + "\nfrom pathlib import Path\n\n" + code
    return code


def _inject_pillow_normalize(code: str) -> str:
    """Wrap raw add_picture calls with Pillow normalization."""
    # Find add_picture calls and wrap with normalize logic
    pattern = r"(doc\.add_picture\(['\"])(workspace/[^'\"]+)(['\"][^)]*\))"

    def replace_with_normalize(m: re.Match) -> str:
        path = m.group(2)
        tmp  = path.replace(".", "_norm.")
        return (
            f"_norm_{hash(path) & 0xFFFF} = '{tmp}'\n"
            f"try:\n"
            f"    from PIL import Image as _PIL\n"
            f"    _img = _PIL.Image.open('{path}').convert('RGB')\n"
            f"    _img.save(_norm_{hash(path) & 0xFFFF})\n"
            f"except Exception: _norm_{hash(path) & 0xFFFF} = '{path}'\n"
            f"doc.add_picture(_norm_{hash(path) & 0xFFFF}" + m.group(3)
        )

    return re.sub(pattern, replace_with_normalize, code)
