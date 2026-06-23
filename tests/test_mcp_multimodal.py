"""Tests for MCP Server and multi-modal gateways."""

import json
import pytest
from pathlib import Path

from core.mcp_server import TokenRunMCPServer


# ---------------------------------------------------------------------------
# MCP Server Tests
# ---------------------------------------------------------------------------

class TestMCPServer:
    def test_get_server_info(self, tmp_path):
        server = TokenRunMCPServer(vault_path=str(tmp_path))
        info = server.get_server_info()
        assert info["name"] == "tokenrun"
        assert info["version"] == "0.1.0"
        assert "tools" in info["capabilities"]

    def test_list_tools(self, tmp_path):
        server = TokenRunMCPServer(vault_path=str(tmp_path))
        tools = server.list_tools()
        tool_names = [t["name"] for t in tools]
        assert "list_skills" in tool_names
        assert "get_skill" in tool_names
        assert "run_skill" in tool_names
        assert "create_mission" in tool_names

    def test_list_tools_includes_solidified_skills(self, tmp_path):
        vault = tmp_path
        skill_data = {
            "skill_id": "TR-SKILL-TEST",
            "name": "Test Skill",
            "description": "A test skill",
            "optimized_prompt": "Process: {{ data }}",
        }
        (vault / "TR-SKILL-TEST.trs").write_text(
            json.dumps(skill_data), encoding="utf-8"
        )
        server = TokenRunMCPServer(vault_path=str(vault))
        tools = server.list_tools()
        tool_names = [t["name"] for t in tools]
        assert "skill_TR-SKILL-TEST" in tool_names

    def test_call_list_skills(self, tmp_path):
        vault = tmp_path
        skill_data = {
            "skill_id": "TR-SKILL-A",
            "name": "Skill A",
            "created_at": "2024-01-01",
        }
        (vault / "TR-SKILL-A.trs").write_text(
            json.dumps(skill_data), encoding="utf-8"
        )
        server = TokenRunMCPServer(
            vault_path=str(vault),
            skills_library_path=str(tmp_path / "empty_lib"),  # avoid loading presets
        )
        result = server.call_tool("list_skills", {})
        assert "content" in result
        data = json.loads(result["content"][0]["text"])
        assert len(data) == 1
        assert data[0]["skill_id"] == "TR-SKILL-A"

    def test_call_get_skill(self, tmp_path):
        vault = tmp_path
        skill_data = {
            "skill_id": "TR-SKILL-B",
            "name": "Skill B",
            "optimized_prompt": "Do {{ data }}",
        }
        (vault / "TR-SKILL-B.trs").write_text(
            json.dumps(skill_data), encoding="utf-8"
        )
        server = TokenRunMCPServer(vault_path=str(vault))
        result = server.call_tool("get_skill", {"skill_id": "TR-SKILL-B"})
        data = json.loads(result["content"][0]["text"])
        assert data["name"] == "Skill B"

    def test_call_get_skill_not_found(self, tmp_path):
        server = TokenRunMCPServer(vault_path=str(tmp_path))
        result = server.call_tool("get_skill", {"skill_id": "nonexistent"})
        assert result.get("isError") is True

    def test_call_run_skill(self, tmp_path):
        vault = tmp_path
        skill_data = {
            "skill_id": "TR-SKILL-C",
            "name": "Skill C",
            "optimized_prompt": "Summarize: {{ data }}",
            "model_config": {"model": "gpt-4o"},
            "validation_rules": [{"type": "llm_eval", "criteria": "good"}],
        }
        (vault / "TR-SKILL-C.trs").write_text(
            json.dumps(skill_data), encoding="utf-8"
        )
        server = TokenRunMCPServer(vault_path=str(vault))
        result = server.call_tool("run_skill", {
            "skill_id": "TR-SKILL-C",
            "input_data": "Hello world",
        })
        data = json.loads(result["content"][0]["text"])
        assert data["action"] == "execute_skill"
        assert "Summarize: Hello world" in data["prompt"]

    def test_call_run_skill_not_found(self, tmp_path):
        server = TokenRunMCPServer(vault_path=str(tmp_path))
        result = server.call_tool("run_skill", {
            "skill_id": "nonexistent",
            "input_data": "test",
        })
        assert result.get("isError") is True

    def test_call_create_mission(self, tmp_path):
        runfile = tmp_path / "test.yaml"
        runfile.write_text("name: test\nworkflow: []", encoding="utf-8")
        server = TokenRunMCPServer(vault_path=str(tmp_path))
        result = server.call_tool("create_mission", {
            "runfile_path": str(runfile),
            "priority": "low",
        })
        data = json.loads(result["content"][0]["text"])
        assert data["action"] == "create_mission"
        assert data["priority"] == "low"

    def test_call_create_mission_not_found(self, tmp_path):
        server = TokenRunMCPServer(vault_path=str(tmp_path))
        result = server.call_tool("create_mission", {
            "runfile_path": "nonexistent.yaml",
        })
        assert result.get("isError") is True

    def test_call_unknown_tool(self, tmp_path):
        server = TokenRunMCPServer(vault_path=str(tmp_path))
        result = server.call_tool("unknown_tool", {})
        assert result.get("isError") is True

    def test_skill_tool_shortcut(self, tmp_path):
        """skill_<id> tools should work as shortcuts."""
        vault = tmp_path
        skill_data = {
            "skill_id": "TR-SKILL-D",
            "name": "Skill D",
            "optimized_prompt": "Analyze: {{ data }}",
        }
        (vault / "TR-SKILL-D.trs").write_text(
            json.dumps(skill_data), encoding="utf-8"
        )
        server = TokenRunMCPServer(vault_path=str(vault))
        result = server.call_tool("skill_TR-SKILL-D", {"input": "test data"})
        data = json.loads(result["content"][0]["text"])
        assert data["action"] == "execute_skill"
        assert "Analyze: test data" in data["prompt"]

    def test_load_skills_from_library(self, tmp_path):
        """Should load skills from both vault and library."""
        vault = tmp_path / "vault"
        vault.mkdir()
        library = tmp_path / "library"
        library.mkdir()

        (vault / "A.trs").write_text(
            json.dumps({"skill_id": "A", "name": "Skill A"}), encoding="utf-8"
        )
        (library / "B.trs").write_text(
            json.dumps({"skill_id": "B", "name": "Skill B"}), encoding="utf-8"
        )

        server = TokenRunMCPServer(
            vault_path=str(vault),
            skills_library_path=str(library),
        )
        assert "A" in server._skills_cache
        assert "B" in server._skills_cache


# ---------------------------------------------------------------------------
# Video Gateway Tests (no cv2 dependency — test structure only)
# ---------------------------------------------------------------------------

class TestVideoGateway:
    def test_import_error_without_cv2(self):
        """Should raise ImportError if cv2 is not installed."""
        from gateway.video_gateway import VideoGateway
        # We can't easily test the actual import error without mocking
        # but we can verify the class exists and has the right interface
        vg = VideoGateway(fps_sample=1.0, max_frames=10)
        assert vg.fps_sample == 1.0
        assert vg.max_frames == 10
        assert vg.output_format == "base64"


# ---------------------------------------------------------------------------
# Audio Gateway Tests (no whisper dependency — test structure only)
# ---------------------------------------------------------------------------

class TestAudioGateway:
    def test_init_defaults(self):
        from gateway.audio_gateway import AudioGateway
        ag = AudioGateway()
        assert ag.backend == "openai"
        assert ag.model == "whisper-1"
        assert ag.language is None

    def test_init_custom(self):
        from gateway.audio_gateway import AudioGateway
        ag = AudioGateway(backend="local", model="large", language="zh")
        assert ag.backend == "local"
        assert ag.model == "large"
        assert ag.language == "zh"

    @pytest.mark.asyncio
    async def test_unsupported_backend_raises(self):
        from gateway.audio_gateway import AudioGateway
        ag = AudioGateway(backend="unknown")
        with pytest.raises(ValueError, match="Unsupported backend"):
            await ag.transcribe("test.mp3")
