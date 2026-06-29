"""Unified security constants for the Ares AI Ecosystem v2.0.

Single source of truth for blocked modules — used by both code_audit.py and sandbox.py.
NOTE: "os" is intentionally NOT blocked — it is required by generated agent scripts
for os.makedirs("workspace", exist_ok=True) and os.path operations.
"""

BLOCKED_MODULES: frozenset[str] = frozenset({
    "subprocess",
    "socket",
    "ctypes",
    "multiprocessing",
    "importlib",
    "pickle",
    "pty",
    "nt",
    "winreg",
    "sys",
})
