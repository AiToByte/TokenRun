"""
MCP Client — connect to external MCP Servers as a client.

Allows TokenRun to call tools on other MCP Servers (e.g., Notion,
GitHub, custom services).  This transforms TokenRun from a standalone
app into a node in the MCP ecosystem.

Usage::

    async with MCPClient("http://localhost:3000") as client:
        tools = await client.list_tools()
        result = await client.call_tool("search", {"query": "AI"})
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx

__all__ = ["MCPClient", "MCPTool"]


class MCPTool:
    """Represents a tool exposed by an MCP Server."""

    def __init__(
        self, name: str, description: str, input_schema: Dict[str, Any]
    ) -> None:
        self.name = name
        self.description = description
        self.input_schema = input_schema

    def __repr__(self) -> str:
        return f"MCPTool({self.name})"


class MCPClient:
    """Client for connecting to MCP Servers.

    Parameters
    ----------
    server_url:
        URL of the MCP Server (e.g., ``http://localhost:3000``).
    timeout:
        Request timeout in seconds.
    """

    def __init__(
        self,
        server_url: str,
        timeout: float = 30.0,
    ) -> None:
        self.server_url = server_url.rstrip("/")
        self.timeout = timeout
        self._client = httpx.AsyncClient(timeout=timeout)
        self._request_id = 0
        self._tools_cache: Optional[List[MCPTool]] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def initialize(self) -> Dict[str, Any]:
        """Initialize connection with the MCP Server.

        Returns server info (name, version, capabilities).
        """
        return await self._send_request("initialize", {})

    async def list_tools(self) -> List[MCPTool]:
        """List available tools on the MCP Server."""
        result = await self._send_request("tools/list", {})
        tools_data = result.get("tools", [])

        tools = []
        for t in tools_data:
            tools.append(
                MCPTool(
                    name=t.get("name", ""),
                    description=t.get("description", ""),
                    input_schema=t.get("inputSchema", {}),
                )
            )
        self._tools_cache = tools
        return tools

    async def call_tool(
        self,
        tool_name: str,
        arguments: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Call a tool on the MCP Server.

        Parameters
        ----------
        tool_name:
            Name of the tool to call.
        arguments:
            Tool-specific arguments.

        Returns
        -------
        dict
            Tool response with ``content`` array.
        """
        return await self._send_request(
            "tools/call",
            {
                "name": tool_name,
                "arguments": arguments or {},
            },
        )

    async def get_tool(self, name: str) -> Optional[MCPTool]:
        """Get a specific tool by name (uses cache if available)."""
        if self._tools_cache is None:
            await self.list_tools()
        for tool in self._tools_cache:
            if tool.name == name:
                return tool
        return None

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> MCPClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _send_request(
        self,
        method: str,
        params: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Send a JSON-RPC 2.0 request to the MCP Server."""
        self._request_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params,
        }

        resp = await self._client.post(
            self.server_url,
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()

        data = resp.json()
        if "error" in data:
            error = data["error"]
            raise RuntimeError(
                f"MCP error {error.get('code', '?')}: {error.get('message', 'Unknown')}"
            )
        return data.get("result", {})
