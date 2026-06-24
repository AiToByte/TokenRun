"""Tests for v1 iteration fixes — sandbox hardening, fingerprint, ledger, MCP client."""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from core.ledger import BudgetExceededError, TokenLedger
from core.runner import ActorCriticLoop
from core.sandbox import SandboxExecutor
from gateway.mcp_client import MCPClient, MCPTool


# ---------------------------------------------------------------------------
# Sandbox Security Hardening
# ---------------------------------------------------------------------------

class TestSandboxSecurity:
    def test_block_network_import(self):
        sandbox = SandboxExecutor(allow_network=False)
        result = sandbox.execute_python("import socket; socket.connect(('localhost', 80))")
        assert result["passed"] is False
        assert "blocked" in result["error"]

    def test_block_httpx_import(self):
        sandbox = SandboxExecutor(allow_network=False)
        result = sandbox.execute_python("import httpx")
        assert result["passed"] is False
        assert "blocked" in result["error"]

    def test_block_subprocess_import(self):
        sandbox = SandboxExecutor(allow_network=False)
        result = sandbox.execute_python("import subprocess")
        assert result["passed"] is False

    def test_allow_network_when_enabled(self):
        sandbox = SandboxExecutor(allow_network=True, allow_file_write=True)
        result = sandbox.execute_python("x = 1 + 1\nassert x == 2")
        assert result["passed"] is True

    def test_restricted_env_removes_secrets(self):
        import os
        os.environ["TEST_API_KEY_SECRET"] = "should-not-leak"
        sandbox = SandboxExecutor()
        env = sandbox._restricted_env()
        assert "TEST_API_KEY_SECRET" not in env
        del os.environ["TEST_API_KEY_SECRET"]

    def test_block_file_write(self):
        sandbox = SandboxExecutor(allow_file_write=False)
        result = sandbox.execute_python(
            "f = open('/tmp/test_sandbox.txt', 'w')\nf.write('test')"
        )
        assert result["passed"] is False
        assert "blocked" in result["error"]


# ---------------------------------------------------------------------------
# Fingerprint Granularity
# ---------------------------------------------------------------------------

class TestFingerprintGranularity:
    def test_fingerprint_includes_temperature(self):
        fp1 = ActorCriticLoop.compute_fingerprint(
            "gpt-4o", "Hello", {"temperature": 0.1}
        )
        fp2 = ActorCriticLoop.compute_fingerprint(
            "gpt-4o", "Hello", {"temperature": 0.9}
        )
        assert fp1.parameters["temperature"] == 0.1
        assert fp2.parameters["temperature"] == 0.9
        # Different temperature should produce different fingerprints
        assert fp1.parameters != fp2.parameters

    def test_fingerprint_includes_seed(self):
        fp = ActorCriticLoop.compute_fingerprint(
            "gpt-4o", "Hello", {"temperature": 0.1, "seed": 42}
        )
        assert fp.parameters["seed"] == 42

    def test_verify_fingerprint_temperature_mismatch(self):
        fp = ActorCriticLoop.compute_fingerprint(
            "gpt-4o", "Hello", {"temperature": 0.1, "seed": 42}
        )
        # Same model and prompt but different temperature
        assert ActorCriticLoop.verify_fingerprint(
            fp, "gpt-4o", "Hello", {"temperature": 0.9, "seed": 42}
        ) is False

    def test_verify_fingerprint_seed_mismatch(self):
        fp = ActorCriticLoop.compute_fingerprint(
            "gpt-4o", "Hello", {"temperature": 0.1, "seed": 42}
        )
        # Same model and prompt but different seed
        assert ActorCriticLoop.verify_fingerprint(
            fp, "gpt-4o", "Hello", {"temperature": 0.1, "seed": 99}
        ) is False

    def test_verify_fingerprint_match(self):
        fp = ActorCriticLoop.compute_fingerprint(
            "gpt-4o", "Hello", {"temperature": 0.1, "seed": 42}
        )
        assert ActorCriticLoop.verify_fingerprint(
            fp, "gpt-4o", "Hello", {"temperature": 0.1, "seed": 42}
        ) is True


# ---------------------------------------------------------------------------
# Token Ledger Thread Safety
# ---------------------------------------------------------------------------

class TestLedgerThreadSafety:
    def test_concurrent_record_usage_accurate(self):
        """Thread-safe recording should produce accurate totals."""
        import threading

        pricing = {"m": {"prompt": 0.001, "completion": 0.001}}
        ledger = TokenLedger(budget_usd=10000.0, pricing_map=pricing)
        errors = []

        def record(i):
            try:
                ledger.record_usage("m", prompt_tokens=10, completion_tokens=5, role="actor")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=record, args=(i,)) for i in range(200)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert ledger.report.call_count == 200
        assert ledger.report.actor_prompt_tokens == 2000
        assert ledger.report.actor_completion_tokens == 1000


# ---------------------------------------------------------------------------
# MCP Client
# ---------------------------------------------------------------------------

class TestMCPClient:
    @pytest.mark.asyncio
    async def test_context_manager(self):
        client = MCPClient("http://localhost:3000")
        async with client as c:
            assert c is client

    @pytest.mark.asyncio
    async def test_list_tools_parses_response(self):
        client = MCPClient("http://localhost:3000")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "tools": [
                    {
                        "name": "search",
                        "description": "Search documents",
                        "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}},
                    }
                ]
            },
        }
        mock_resp.raise_for_status = MagicMock()
        client._client = MagicMock()
        client._client.post = AsyncMock(return_value=mock_resp)
        client._client.aclose = AsyncMock()

        tools = await client.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "search"
        assert tools[0].description == "Search documents"
        await client.close()

    @pytest.mark.asyncio
    async def test_call_tool(self):
        client = MCPClient("http://localhost:3000")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "content": [{"type": "text", "text": "result data"}],
            },
        }
        mock_resp.raise_for_status = MagicMock()
        client._client = MagicMock()
        client._client.post = AsyncMock(return_value=mock_resp)
        client._client.aclose = AsyncMock()

        result = await client.call_tool("search", {"query": "AI"})
        assert "content" in result
        assert result["content"][0]["text"] == "result data"
        await client.close()

    @pytest.mark.asyncio
    async def test_error_response_raises(self):
        client = MCPClient("http://localhost:3000")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -32601, "message": "Method not found"},
        }
        mock_resp.raise_for_status = MagicMock()
        client._client = MagicMock()
        client._client.post = AsyncMock(return_value=mock_resp)
        client._client.aclose = AsyncMock()

        with pytest.raises(RuntimeError, match="Method not found"):
            await client.list_tools()
        await client.close()

    def test_mcp_tool_repr(self):
        tool = MCPTool("search", "Search docs", {})
        assert repr(tool) == "MCPTool(search)"
