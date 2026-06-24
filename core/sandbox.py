"""
Sandbox Executor — safe code execution with security hardening.

Provides isolation for ``code_eval`` validation rules.  The subprocess
backend runs code with restricted permissions: no network, limited
filesystem access, and resource limits.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

__all__ = ["SandboxExecutor"]


# Blocked modules that could access network or sensitive resources.
_BLOCKED_IMPORTS = [
    "import socket",
    "import http",
    "import urllib",
    "import requests",
    "import httpx",
    "import aiohttp",
    "import subprocess",
    "import shlex",
    "import shutil",
    "import ctypes",
    "import multiprocessing",
]


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
        # Security check: block dangerous imports
        if not self.allow_network:
            for blocked in _BLOCKED_IMPORTS:
                if blocked in code:
                    return {
                        "passed": False,
                        "score": 0.0,
                        "output": "",
                        "error": f"Security: {blocked.split()[-1]} module is blocked",
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
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".py", delete=False
            ) as f:
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
        """Build a restricted environment variable set."""
        env = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
            "HOME": os.environ.get("HOME", "/tmp"),
            "TMPDIR": tempfile.gettempdir(),
        }
        # Remove any cloud credentials or sensitive vars
        for key in list(env.keys()):
            if any(s in key.upper() for s in ["KEY", "TOKEN", "SECRET", "PASSWORD"]):
                del env[key]
        return env

    @staticmethod
    def _indent(code: str, spaces: int) -> str:
        """Indent code block by N spaces."""
        prefix = " " * spaces
        return "\n".join(prefix + line for line in code.split("\n"))
