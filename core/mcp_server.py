"""
MCP Server — expose TokenRun skills as MCP tools.

Allows Claude Desktop and other MCP-compatible clients to directly
call TokenRun's solidified skills.  Transforms TokenRun from a
standalone app into an intelligent asset provider.

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


class TokenRunMCPServer:
    """MCP Server exposing TokenRun skills as callable tools.

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

    # ------------------------------------------------------------------
    # MCP Protocol Methods
    # ------------------------------------------------------------------

    def get_server_info(self) -> Dict[str, Any]:
        """Return MCP server metadata."""
        return {
            "name": "tokenrun",
            "version": "0.1.0",
            "description": "TokenRun — Industrial AI task execution. Exposes solidified skills as MCP tools.",
            "capabilities": {
                "tools": {},
            },
        }

    def list_tools(self) -> List[Dict[str, Any]]:
        """Return available MCP tools."""
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
                "description": "Execute a solidified skill with the given input data. Returns the processed output.",
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
                            "description": "Task priority (low = Batch API for cost savings).",
                            "default": "normal",
                        },
                    },
                    "required": ["runfile_path"],
                },
            },
        ]

        # Add a tool for each solidified skill
        for skill_id, skill in self._skills_cache.items():
            tools.append({
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
            })

        return tools

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Handle an MCP tool call.

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
                return self._handle_run_skill(arguments["skill_id"], arguments["input_data"])
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
    # Tool handlers
    # ------------------------------------------------------------------

    def _handle_list_skills(self) -> Dict[str, Any]:
        skills_list = []
        for skill_id, skill in self._skills_cache.items():
            skills_list.append({
                "skill_id": skill_id,
                "name": skill.get("name", "Unknown"),
                "description": skill.get("description", ""),
                "created_at": skill.get("created_at", ""),
            })
        return {
            "content": [{"type": "text", "text": json.dumps(skills_list, ensure_ascii=False, indent=2)}],
        }

    def _handle_get_skill(self, skill_id: str) -> Dict[str, Any]:
        skill = self._skills_cache.get(skill_id)
        if not skill:
            return self._error_response(f"Skill not found: {skill_id}")
        return {
            "content": [{"type": "text", "text": json.dumps(skill, ensure_ascii=False, indent=2)}],
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
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "action": "execute_skill",
                    "skill_id": skill_id,
                    "prompt": prompt.replace("{{ data }}", input_data),
                    "model_config": skill.get("model_config", {}),
                    "validation_rules": skill.get("validation_rules", []),
                }, ensure_ascii=False, indent=2),
            }],
        }

    def _handle_create_mission(self, runfile_path: str, priority: str) -> Dict[str, Any]:
        path = Path(runfile_path)
        if not path.exists():
            return self._error_response(f"Runfile not found: {runfile_path}")
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "action": "create_mission",
                    "runfile_path": runfile_path,
                    "priority": priority,
                    "status": "ready",
                }, ensure_ascii=False, indent=2),
            }],
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
        """Start the MCP server (stdio mode for Claude Desktop compatibility)."""
        # MCP uses stdio by default — read JSON-RPC from stdin, write to stdout
        print(f"TokenRun MCP Server started. Skills loaded: {len(self._skills_cache)}", file=sys.stderr)
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
