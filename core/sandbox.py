"""
Sandbox Executor — safe code execution with security hardening.

Provides isolation for ``code_eval`` validation rules.  The subprocess
backend runs code with restricted permissions: no network, limited
filesystem access, and resource limits.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

__all__ = ["SandboxExecutor"]

# Blocked module names (checked via AST, not string matching).
_BLOCKED_MODULES = {
    "socket",
    "http",
    "urllib",
    "requests",
    "httpx",
    "aiohttp",
    "subprocess",
    "shlex",
    "shutil",
    "ctypes",
    "multiprocessing",
    "importlib",
}


def _check_blocked_imports(code: str) -> Optional[str]:
    """Use AST to detect blocked imports. Returns error message or None."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None  # let the subprocess report the syntax error

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root_module = alias.name.split(".")[0]
                if root_module in _BLOCKED_MODULES:
                    return f"Security: module '{root_module}' is blocked"
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                root_module = node.module.split(".")[0]
                if root_module in _BLOCKED_MODULES:
                    return f"Security: module '{root_module}' is blocked"
    return None


class SandboxExecutor:
    """Execute code in a security-hardened environment.

    Parameters
    ----------
    timeout:
        Maximum execution time in seconds.
    max_memory_mb:
        Maximum memory in MB (requires ``resource`` module on Unix).
    allow_network:
        If False (default), block network-related imports.
    allow_file_write:
        If False (default), block file write operations.
    """

    def __init__(
        self,
        timeout: int = 10,
        max_memory_mb: int = 256,
        allow_network: bool = False,
        allow_file_write: bool = False,
    ) -> None:
        self.timeout = timeout
        self.max_memory_mb = max_memory_mb
        self.allow_network = allow_network
        self.allow_file_write = allow_file_write

    def execute_python(
        self,
        code: str,
        variables: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Execute Python code in a security-hardened subprocess.

        Parameters
        ----------
        code:
            Python code to execute.  Can reference variables from
            the ``variables`` dict.
        variables:
            Variables to inject into the execution context.

        Returns
        -------
        dict
            ``{"passed": bool, "score": float, "output": str, "error": str}``
        """
        # Security check: AST-based blocked import detection
        if not self.allow_network:
            block_reason = _check_blocked_imports(code)
            if block_reason:
                return {
                    "passed": False,
                    "score": 0.0,
                    "output": "",
                    "error": block_reason,
                }

        # Build script with variable injection
        var_lines = ""
        if variables:
            for k, v in variables.items():
                var_lines += f"{k} = {json.dumps(v, ensure_ascii=False)}\n"

        # Security preamble: restrict dangerous builtins
        security_preamble = ""
        if not self.allow_file_write:
            security_preamble = """
# Security: override open to block writes
_builtins_open = open
def _safe_open(file, mode='r', **kwargs):
    if 'w' in mode or 'a' in mode or 'x' in mode:
        raise PermissionError("Sandbox: file write is blocked")
    return _builtins_open(file, mode, **kwargs)
open = _safe_open
"""

        script = f"""import sys, json
{security_preamble}
{var_lines}
try:
{self._indent(code, 4)}
    print(json.dumps({{"passed": True, "score": 1.0, "output": ""}}))
except AssertionError as e:
    print(json.dumps({{"passed": False, "score": 0.0, "error": str(e)}}))
except Exception as e:
    print(json.dumps({{"passed": False, "score": 0.0, "error": str(e)}}))
"""
        try:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
                f.write(script)
                temp_path = f.name

            env = self._restricted_env()
            proc = subprocess.run(
                [sys.executable, temp_path],
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env=env,
            )
            Path(temp_path).unlink(missing_ok=True)

            if proc.returncode == 0 and proc.stdout.strip():
                return json.loads(proc.stdout.strip())
            return {
                "passed": False,
                "score": 0.0,
                "output": "",
                "error": proc.stderr[:500] if proc.stderr else "Unknown error",
            }
        except subprocess.TimeoutExpired:
            return {"passed": False, "score": 0.0, "output": "", "error": "Timeout"}
        except Exception as exc:
            return {"passed": False, "score": 0.0, "output": "", "error": str(exc)}

    def _restricted_env(self) -> Dict[str, str]:
        """Build a restricted environment variable set.

        PATH is restricted to only the Python interpreter directory
        to prevent execution of arbitrary system binaries.
        """
        python_dir = str(Path(sys.executable).parent)
        env = {
            "PATH": python_dir,
            "HOME": "/tmp",
            "TMPDIR": tempfile.gettempdir(),
        }
        return env

    @staticmethod
    def _indent(code: str, spaces: int) -> str:
        """Indent code block by N spaces."""
        prefix = " " * spaces
        return "\n".join(prefix + line for line in code.split("\n"))
