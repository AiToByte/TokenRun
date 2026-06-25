"""
MCP Server — FastMCP-based implementation exposing TokenRun skills as MCP tools.

Allows Claude Desktop and other MCP-compatible clients to directly
call TokenRun's solidified skills.  Uses FastMCP SDK for clean
decorator-based tool registration.

Usage::

    # As a standalone MCP server
    python -m core.mcp_server

    # Or import and customize
    from core.mcp_server import TokenRunMCPServer
    server = TokenRunMCPServer(vault_path="./vault")
    server.run()
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

__all__ = ["TokenRunMCPServer"]

# ---------------------------------------------------------------------------
# FastMCP import with graceful fallback
# ---------------------------------------------------------------------------

try:
    from mcp.server.fastmcp import FastMCP

    _FASTMCP_AVAILABLE = True
except ImportError:
    _FASTMCP_AVAILABLE = False


# ---------------------------------------------------------------------------
# TokenRunMCPServer (backward-compatible wrapper)
# ---------------------------------------------------------------------------


class TokenRunMCPServer:
    """MCP Server exposing TokenRun skills as callable tools.

    Uses FastMCP SDK when available for clean decorator-based tool
    registration.  Falls back to legacy JSON-RPC stdio mode.

    Parameters
    ----------
    vault_path:
        Path to the directory containing .trs skill files.
    skills_library_path:
        Path to the preset skills library.
    """

    def __init__(
        self,
        vault_path: str = "vault",
        skills_library_path: str = "skills/library",
    ) -> None:
        self.vault_path = Path(vault_path)
        self.skills_library_path = Path(skills_library_path)
        self._skills_cache: Dict[str, Dict[str, Any]] = {}
        self._load_skills()

        # Create FastMCP server if available
        self._mcp: Optional[Any] = None
        if _FASTMCP_AVAILABLE:
            self._mcp = FastMCP(
                name="tokenrun",
                version="0.2.0",
                description=(
                    "TokenRun — Industrial AI task execution. "
                    "Exposes solidified skills as MCP tools."
                ),
            )
            self._register_fastmcp_tools()

    # ------------------------------------------------------------------
    # FastMCP tool registration
    # ------------------------------------------------------------------

    def _register_fastmcp_tools(self) -> None:
        """Register tools using FastMCP decorators."""
        if self._mcp is None:
            return

        skills_cache = self._skills_cache  # capture for closures

        @self._mcp.tool()
        async def list_skills() -> str:
            """List all available solidified skills in the vault."""
            skills_list = []
            for skill_id, skill in skills_cache.items():
                skills_list.append(
                    {
                        "skill_id": skill_id,
                        "name": skill.get("name", "Unknown"),
                        "description": skill.get("description", ""),
                        "created_at": skill.get("created_at", ""),
                    }
                )
            return json.dumps(skills_list, ensure_ascii=False, indent=2)

        @self._mcp.tool()
        async def get_skill(skill_id: str) -> str:
            """Get details of a specific skill by ID."""
            skill = skills_cache.get(skill_id)
            if not skill:
                return f"Error: Skill not found: {skill_id}"
            return json.dumps(skill, ensure_ascii=False, indent=2)

        @self._mcp.tool()
        async def run_skill(skill_id: str, input_data: str) -> str:
            """Execute a solidified skill with the given input data.

            Returns the processed output with prompt, model config,
            and validation rules.
            """
            skill = skills_cache.get(skill_id)
            if not skill:
                return f"Error: Skill not found: {skill_id}"

            prompt = skill.get("optimized_prompt", "")
            if not prompt:
                return f"Error: Skill {skill_id} has no optimized prompt"

            result = {
                "action": "execute_skill",
                "skill_id": skill_id,
                "prompt": prompt.replace("{{ data }}", input_data),
                "model_config": skill.get("model_config", {}),
                "validation_rules": skill.get("validation_rules", []),
            }
            return json.dumps(result, ensure_ascii=False, indent=2)

        @self._mcp.tool()
        async def create_mission(runfile_path: str, priority: str = "normal") -> str:
            """Create and start a new TokenRun mission from a Runfile.

            Parameters
            ----------
            runfile_path:
                Path to the YAML Runfile.
            priority:
                Task priority: high, normal, or low (low = Batch API for
                cost savings).
            """
            path = Path(runfile_path)
            if not path.exists():
                return f"Error: Runfile not found: {runfile_path}"

            result = {
                "action": "create_mission",
                "runfile_path": runfile_path,
                "priority": priority,
                "status": "ready",
            }
            return json.dumps(result, ensure_ascii=False, indent=2)

    # ------------------------------------------------------------------
    # MCP Protocol Methods (legacy JSON-RPC for non-FastMCP mode)
    # ------------------------------------------------------------------

    def get_server_info(self) -> Dict[str, Any]:
        """Return MCP server metadata."""
        return {
            "name": "tokenrun",
            "version": "0.2.0",
            "description": (
                "TokenRun — Industrial AI task execution. "
                "Exposes solidified skills as MCP tools."
            ),
            "capabilities": {
                "tools": {},
            },
        }

    def list_tools(self) -> List[Dict[str, Any]]:
        """Return available MCP tools (legacy method)."""
        tools = [
            {
                "name": "list_skills",
                "description": "List all available solidified skills in the vault.",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
            {
                "name": "get_skill",
                "description": "Get details of a specific skill by ID.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "skill_id": {
                            "type": "string",
                            "description": "The skill ID (e.g., TR-SKILL-abc123)",
                        },
                    },
                    "required": ["skill_id"],
                },
            },
            {
                "name": "run_skill",
                "description": (
                    "Execute a solidified skill with the given input data. "
                    "Returns the processed output."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "skill_id": {
                            "type": "string",
                            "description": "The skill ID to execute.",
                        },
                        "input_data": {
                            "type": "string",
                            "description": "The input data to process.",
                        },
                    },
                    "required": ["skill_id", "input_data"],
                },
            },
            {
                "name": "create_mission",
                "description": "Create and start a new TokenRun mission from a Runfile.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "runfile_path": {
                            "type": "string",
                            "description": "Path to the YAML Runfile.",
                        },
                        "priority": {
                            "type": "string",
                            "enum": ["high", "normal", "low"],
                            "description": (
                                "Task priority (low = Batch API for cost savings)."
                            ),
                            "default": "normal",
                        },
                    },
                    "required": ["runfile_path"],
                },
            },
        ]

        # Add a tool for each solidified skill
        for skill_id, skill in self._skills_cache.items():
            tools.append(
                {
                    "name": f"skill_{skill_id}",
                    "description": (
                        f"Run the '{skill.get('name', skill_id)}' skill. "
                        f"{skill.get('description', 'No description.')}"
                    ),
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "input": {
                                "type": "string",
                                "description": "Input data to process.",
                            },
                        },
                        "required": ["input"],
                    },
                }
            )

        return tools

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Handle an MCP tool call (legacy method).

        Returns
        -------
        dict
            MCP-formatted response with ``content`` array.
        """
        try:
            if name == "list_skills":
                return self._handle_list_skills()
            elif name == "get_skill":
                return self._handle_get_skill(arguments["skill_id"])
            elif name == "run_skill":
                return self._handle_run_skill(
                    arguments["skill_id"], arguments["input_data"]
                )
            elif name == "create_mission":
                return self._handle_create_mission(
                    arguments["runfile_path"],
                    arguments.get("priority", "normal"),
                )
            elif name.startswith("skill_"):
                skill_id = name[6:]  # remove "skill_" prefix
                return self._handle_run_skill(skill_id, arguments["input"])
            else:
                return self._error_response(f"Unknown tool: {name}")
        except Exception as exc:
            return self._error_response(str(exc))

    # ------------------------------------------------------------------
    # Tool handlers (legacy)
    # ------------------------------------------------------------------

    def _handle_list_skills(self) -> Dict[str, Any]:
        skills_list = []
        for skill_id, skill in self._skills_cache.items():
            skills_list.append(
                {
                    "skill_id": skill_id,
                    "name": skill.get("name", "Unknown"),
                    "description": skill.get("description", ""),
                    "created_at": skill.get("created_at", ""),
                }
            )
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(skills_list, ensure_ascii=False, indent=2),
                }
            ],
        }

    def _handle_get_skill(self, skill_id: str) -> Dict[str, Any]:
        skill = self._skills_cache.get(skill_id)
        if not skill:
            return self._error_response(f"Skill not found: {skill_id}")
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(skill, ensure_ascii=False, indent=2),
                }
            ],
        }

    def _handle_run_skill(self, skill_id: str, input_data: str) -> Dict[str, Any]:
        skill = self._skills_cache.get(skill_id)
        if not skill:
            return self._error_response(f"Skill not found: {skill_id}")

        prompt = skill.get("optimized_prompt", "")
        if not prompt:
            return self._error_response(f"Skill {skill_id} has no optimized prompt")

        # Return the prompt and config for the MCP client to execute
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "action": "execute_skill",
                            "skill_id": skill_id,
                            "prompt": prompt.replace("{{ data }}", input_data),
                            "model_config": skill.get("model_config", {}),
                            "validation_rules": skill.get("validation_rules", []),
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                }
            ],
        }

    def _handle_create_mission(
        self, runfile_path: str, priority: str
    ) -> Dict[str, Any]:
        path = Path(runfile_path)
        if not path.exists():
            return self._error_response(f"Runfile not found: {runfile_path}")
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "action": "create_mission",
                            "runfile_path": runfile_path,
                            "priority": priority,
                            "status": "ready",
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                }
            ],
        }

    def _error_response(self, message: str) -> Dict[str, Any]:
        return {
            "content": [{"type": "text", "text": f"Error: {message}"}],
            "isError": True,
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _load_skills(self) -> None:
        """Load all .trs files from vault and skills library."""
        for path in [self.vault_path, self.skills_library_path]:
            if path.exists():
                for f in path.glob("*.trs"):
                    try:
                        data = json.loads(f.read_text(encoding="utf-8"))
                        skill_id = data.get("skill_id", f.stem)
                        self._skills_cache[skill_id] = data
                    except (json.JSONDecodeError, OSError):
                        pass

    def run(self, host: str = "0.0.0.0", port: int = 8080) -> None:
        """Start the MCP server.

        Uses FastMCP stdio transport if available, otherwise falls back
        to legacy JSON-RPC stdio mode.
        """
        if self._mcp is not None:
            # FastMCP mode — use stdio transport for Claude Desktop
            print(
                f"TokenRun MCP Server (FastMCP) started. "
                f"Skills loaded: {len(self._skills_cache)}",
                file=sys.stderr,
            )
            self._mcp.run(transport="stdio")
        else:
            # Legacy JSON-RPC stdio mode
            print(
                f"TokenRun MCP Server (legacy) started. "
                f"Skills loaded: {len(self._skills_cache)}",
                file=sys.stderr,
            )
            for line in sys.stdin:
                try:
                    request = json.loads(line.strip())
                    method = request.get("method", "")
                    params = request.get("params", {})
                    req_id = request.get("id")

                    if method == "initialize":
                        result = self.get_server_info()
                    elif method == "tools/list":
                        result = {"tools": self.list_tools()}
                    elif method == "tools/call":
                        tool_name = params.get("name", "")
                        tool_args = params.get("arguments", {})
                        result = self.call_tool(tool_name, tool_args)
                    else:
                        result = {"error": f"Unknown method: {method}"}

                    response = {"jsonrpc": "2.0", "id": req_id, "result": result}
                    print(json.dumps(response), flush=True)

                except json.JSONDecodeError:
                    continue
                except Exception as exc:
                    error_resp = {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -1, "message": str(exc)},
                    }
                    print(json.dumps(error_resp), flush=True)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Run TokenRun as an MCP server."""
    server = TokenRunMCPServer()
    server.run()


if __name__ == "__main__":
    main()
