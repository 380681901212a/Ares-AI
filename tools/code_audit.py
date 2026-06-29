import ast

from tools.security_constants import BLOCKED_MODULES


def static_code_audit(code: str) -> list[str]:
    """Statically audits generated Python code for dangerous imports.

    Uses the unified BLOCKED_MODULES set from security_constants to ensure
    consistency with the sandbox execution environment.
    """
    issues = []
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return [f"SyntaxError in generated code: {exc}"]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in BLOCKED_MODULES:
                    issues.append(f"Dangerous import detected: 'import {alias.name}'")
        elif isinstance(node, ast.ImportFrom):
            module = (node.module or "").split(".")[0]
            if module in BLOCKED_MODULES:
                issues.append(f"Dangerous import detected: 'from {node.module} import ...'")
    return issues
