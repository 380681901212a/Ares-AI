import os
import pathlib
import re
import subprocess
import sys
import tempfile
from typing import List, Optional

from tools.security_constants import BLOCKED_MODULES

BASE_DIR = pathlib.Path(__file__).parent.parent.resolve()
SANDBOX_ENV_PATH = BASE_DIR / "sandbox_env"
SANDBOX_PYTHON = str(SANDBOX_ENV_PATH / "Scripts" / "python.exe")


def _normalize_dependency_name(dependency: str) -> str:
    match = re.match(r"^[A-Za-z0-9_.-]+", dependency.strip())
    return match.group(0).lower() if match else ""


def execute_python_code(
    code: str,
    dependencies: Optional[List[str]] = None,
    workdir: Optional[str] = None,
) -> str:
    """Executes Python code in the sandbox virtual environment.

    Security: uses the unified BLOCKED_MODULES set from security_constants.
    Note: "os" is intentionally allowed — agents need os.makedirs("workspace").
    """
    if dependencies:
        deps_to_install = []
        for dependency in dependencies:
            dep_name = _normalize_dependency_name(dependency)
            if not dep_name:
                return f"Error installing dependencies:\nInvalid dependency name: {dependency}"
            top_level_dep = dep_name.split(".")[0]
            if top_level_dep not in sys.stdlib_module_names:
                if top_level_dep in BLOCKED_MODULES or dep_name in BLOCKED_MODULES:
                    return f"Error installing dependencies:\nBlocked dependency requested: {dependency}"
                deps_to_install.append(dependency)

        pip_path = str(SANDBOX_ENV_PATH / "Scripts" / "pip.exe")
        if deps_to_install:
            try:
                subprocess.run(
                    [pip_path, "install", *deps_to_install],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
            except subprocess.CalledProcessError as exc:
                return f"Error installing dependencies:\n{exc.stderr}"
            except subprocess.TimeoutExpired:
                return "Error installing dependencies:\nTimed out."
            except Exception as exc:
                return f"Error installing dependencies:\n{exc}"

    fd, temp_file_path = tempfile.mkstemp(suffix=".py", text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(code)

        process = subprocess.Popen(
            [SANDBOX_PYTHON, temp_file_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            cwd=workdir or None,
        )

        output_lines = []
        try:
            for line in process.stdout or []:
                print(line, end="", flush=True)
                output_lines.append(line)
            timeout = int(os.getenv("SANDBOX_TIMEOUT_SECONDS", "120"))
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            return f"Error:\nExecution timed out after {timeout} seconds."

        final_output = "".join(output_lines)
        if process.returncode == 0:
            return f"Success:\n{final_output}"
        return f"Error:\n{final_output}"

    except Exception as exc:
        return f"Error:\n{exc}"
    finally:
        try:
            os.remove(temp_file_path)
        except OSError:
            pass
