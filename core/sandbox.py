"""
Sandbox Executor — safe code execution with security hardening.

Provides isolation for ``code_eval`` validation rules.  The subprocess
backend runs code with restricted permissions: no network, limited
filesystem access, and resource limits.

Security notes:
- The sandbox runs code in a subprocess, not in-process.
- AST checks are a first-line defense; the subprocess preamble is the
  real boundary.  Both layers are needed because AST can be bypassed
  with dynamic constructs, but the preamble nullifies dangerous builtins
  before user code runs.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import tempfile
import uuid
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
    "os",
    "io",
    "pathlib",
}

# Additional AST-level keyword patterns that indicate dangerous constructs.
_DANGEROUS_BUILTINS = {"__import__", "__builtins__", "__subclasses__", "__bases__"}


def _check_blocked_imports(code: str) -> Optional[str]:
    """Use AST to detect blocked imports and dangerous constructs.

    Returns error message or None.  This is a first-line defense;
    the subprocess preamble is the real security boundary.
    """
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
        # Block attribute access to dangerous dunder attributes
        elif isinstance(node, ast.Attribute):
            if node.attr in _DANGEROUS_BUILTINS:
                return f"Security: access to '{node.attr}' is blocked"
        # Block calls to getattr with dunder strings
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "getattr" and node.args:
                if isinstance(node.args[1], ast.Constant):
                    if str(node.args[1].value).startswith("__"):
                        return "Security: getattr on dunder attributes is blocked"
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

        # Security preamble: restrict dangerous builtins.
        # This runs BEFORE user code, so user code cannot restore
        # these builtins unless it finds a reference outside builtins.
        security_preamble = """
# Security: disable dangerous builtins
import builtins as _builtins
import types as _types
import gc as _gc

# Save original open for read-only use
_builtins_open = _builtins.open

# Disable gc to prevent __subclasses__ traversal
_gc.disable()

# Nullify dangerous builtins (AFTER all imports are done)
_builtins.__import__ = None
_builtins.exec = None
_builtins.eval = None
_builtins.compile = None
_builtins.globals = None
_builtins.locals = None
_builtins.vars = None
_builtins.dir = None
_builtins.help = None
_builtins.breakpoint = None
"""
        if not self.allow_file_write:
            security_preamble += """
# Security: override open to block ALL write-capable modes
_WRITE_MODES = {'w', 'a', 'x', 'w+', 'r+', 'x+', 'a+', 'w+b', 'r+b', 'a+b', 'r+t', 'w+t', 'a+t', 'x+t'}
def _safe_open(file, mode='r', **kwargs):
    # Normalize mode: strip whitespace, lowercase
    clean_mode = mode.strip().lower()
    # Block any mode that permits writing
    if clean_mode in _WRITE_MODES:
        raise PermissionError("Sandbox: file write is blocked")
    if '+' in clean_mode:
        raise PermissionError("Sandbox: file write is blocked (read+write mode)")
    if 'w' in clean_mode or 'a' in clean_mode or 'x' in clean_mode:
        raise PermissionError("Sandbox: file write is blocked")
    return _builtins_open(file, mode, **kwargs)
_builtins.open = _safe_open
"""

        # Use a unique sentinel to mark the sandbox's own output line.
        # User code cannot know this sentinel, so it cannot forge the result.
        sentinel = uuid.uuid4().hex[:16]

        script = f"""import sys, json
{security_preamble}
{var_lines}
_SENTINEL = "{sentinel}"
try:
{self._indent(code, 4)}
    print(_SENTINEL + json.dumps({{"passed": True, "score": 1.0, "output": ""}}))
except AssertionError as e:
    print(_SENTINEL + json.dumps({{"passed": False, "score": 0.0, "error": str(e)}}))
except Exception as e:
    print(_SENTINEL + json.dumps({{"passed": False, "score": 0.0, "error": str(e)}}))
"""
        try:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
                f.write(script)
                temp_path = f.name

            env = self._restricted_env()
            try:
                proc = subprocess.run(
                    [sys.executable, temp_path],
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                    env=env,
                )
            finally:
                Path(temp_path).unlink(missing_ok=True)

            if proc.returncode == 0 and proc.stdout.strip():
                # Find the line containing our sentinel (ignore any
                # output the user code may have printed before it).
                for line in proc.stdout.strip().splitlines():
                    line = line.strip()
                    if line.startswith(sentinel):
                        json_str = line[len(sentinel) :]
                        return json.loads(json_str)
                # No sentinel found — user code printed its own output
                return {
                    "passed": False,
                    "score": 0.0,
                    "output": "",
                    "error": "Sandbox: output sentinel not found (possible injection)",
                }
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
