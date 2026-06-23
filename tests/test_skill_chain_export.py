"""Tests for Skill chaining and Knowledge Distillation Export."""

import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from core.models import LoopConfig, Runfile, TaskNode, ValidationRule
from core.orchestrator import TROrchestrator
from core.runner import ActorCriticLoop
from core.ledger import TokenLedger
from core.solidifier import SkillSolidifier


# ---------------------------------------------------------------------------
# Skill Recursive Chaining
# ---------------------------------------------------------------------------

class TestSkillRef:
    def test_skill_ref_field(self):
        """TaskNode should support skill_ref field."""
        node = TaskNode(
            id="n1", name="N1",
            skill_ref="skills/library/summarizer.trs",
        )
        assert node.skill_ref == "skills/library/summarizer.trs"
        assert node.actor_prompt_template == ""  # not required when skill_ref is set

    def test_skill_ref_optional(self):
        """skill_ref should be optional."""
        node = TaskNode(
            id="n1", name="N1",
            actor_prompt_template="Do {{ data }}",
        )
        assert node.skill_ref is None


class TestOrchestratorSkillResolution:
    def test_resolve_skill_ref_from_vault(self, tmp_path):
        """Should load .trs from vault/ directory."""
        # Create a skill file
        vault = tmp_path / "vault"
        vault.mkdir()
        skill_data = {
            "skill_id": "TR-SKILL-TEST",
            "name": "Test Skill",
            "optimized_prompt": "Summarize: {{ data }}",
            "validation_rules": [
                {"type": "llm_eval", "criteria": "Must be good"}
            ],
        }
        skill_file = vault / "TR-SKILL-TEST.trs"
        skill_file.write_text(json.dumps(skill_data), encoding="utf-8")

        node = TaskNode(
            id="n1", name="N1",
            skill_ref=str(skill_file),
        )

        engine = MagicMock(spec=ActorCriticLoop)
        ledger = TokenLedger(budget_usd=10.0)
        runfile = Runfile(workflow=[node])
        orch = TROrchestrator(runfile=runfile, loop_engine=engine, ledger=ledger)

        resolved = orch.resolve_skill_ref(node)
        assert resolved.actor_prompt_template == "Summarize: {{ data }}"
        assert len(resolved.loop_config.exit_criteria) == 1

    def test_resolve_skill_ref_by_id(self, tmp_path):
        """Should find .trs by skill_id in vault/."""
        vault = tmp_path / "vault"
        vault.mkdir()
        skill_data = {
            "skill_id": "TR-SKILL-ABC",
            "optimized_prompt": "Process: {{ data }}",
            "validation_rules": [],
        }
        (vault / "TR-SKILL-ABC.trs").write_text(
            json.dumps(skill_data), encoding="utf-8"
        )

        node = TaskNode(id="n1", name="N1", skill_ref="TR-SKILL-ABC")

        engine = MagicMock(spec=ActorCriticLoop)
        ledger = TokenLedger(budget_usd=10.0)
        runfile = Runfile(workflow=[node])
        orch = TROrchestrator(runfile=runfile, loop_engine=engine, ledger=ledger)

        # Change cwd to tmp_path so vault/ is found
        import os
        original_cwd = os.getcwd()
        os.chdir(str(tmp_path))
        try:
            resolved = orch.resolve_skill_ref(node)
            assert resolved.actor_prompt_template == "Process: {{ data }}"
        finally:
            os.chdir(original_cwd)

    def test_resolve_no_skill_ref_returns_unchanged(self):
        """Node without skill_ref should be returned as-is."""
        node = TaskNode(
            id="n1", name="N1",
            actor_prompt_template="Original: {{ data }}",
        )
        engine = MagicMock(spec=ActorCriticLoop)
        ledger = TokenLedger(budget_usd=10.0)
        runfile = Runfile(workflow=[node])
        orch = TROrchestrator(runfile=runfile, loop_engine=engine, ledger=ledger)

        resolved = orch.resolve_skill_ref(node)
        assert resolved.actor_prompt_template == "Original: {{ data }}"

    def test_resolve_nonexistent_skill_raises(self):
        """Non-existent skill_ref should raise FileNotFoundError."""
        node = TaskNode(id="n1", name="N1", skill_ref="nonexistent.trs")
        engine = MagicMock(spec=ActorCriticLoop)
        ledger = TokenLedger(budget_usd=10.0)
        runfile = Runfile(workflow=[node])
        orch = TROrchestrator(runfile=runfile, loop_engine=engine, ledger=ledger)

        with pytest.raises(FileNotFoundError, match="技能文件不存在"):
            orch.resolve_skill_ref(node)


# ---------------------------------------------------------------------------
# Knowledge Distillation Export
# ---------------------------------------------------------------------------

def _traces(success_count=5, min_score=0.9):
    traces = []
    for i in range(success_count):
        traces.append({
            "status": "success",
            "history": [
                {"iteration": 1, "output": f"Input context {i}", "passed": True, "score": min_score + i * 0.01, "critique": None}
            ],
            "final_output": f"Refined output {i}",
        })
    return traces


class TestFineTuneExport:
    def test_export_openai_format(self, tmp_path):
        s = SkillSolidifier(vault_path=str(tmp_path))
        traces = _traces(3)
        path = s.export_fine_tune(traces, format="openai")
        assert path.endswith(".jsonl")

        lines = Path(path).read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 3
        for line in lines:
            data = json.loads(line)
            assert "messages" in data
            assert data["messages"][0]["role"] == "user"
            assert data["messages"][1]["role"] == "assistant"

    def test_export_alpaca_format(self, tmp_path):
        s = SkillSolidifier(vault_path=str(tmp_path))
        traces = _traces(2)
        path = s.export_fine_tune(traces, format="alpaca")

        lines = Path(path).read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2
        for line in lines:
            data = json.loads(line)
            assert "instruction" in data
            assert "output" in data

    def test_export_sharegpt_format(self, tmp_path):
        s = SkillSolidifier(vault_path=str(tmp_path))
        traces = _traces(2)
        path = s.export_fine_tune(traces, format="sharegpt")

        lines = Path(path).read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2
        for line in lines:
            data = json.loads(line)
            assert "conversations" in data
            assert data["conversations"][0]["from"] == "human"
            assert data["conversations"][1]["from"] == "gpt"

    def test_export_filters_by_min_score(self, tmp_path):
        s = SkillSolidifier(vault_path=str(tmp_path))
        traces = [
            {"status": "success", "history": [{"output": "a", "score": 0.9}], "final_output": "A"},
            {"status": "success", "history": [{"output": "b", "score": 0.3}], "final_output": "B"},
            {"status": "success", "history": [{"output": "c", "score": 0.95}], "final_output": "C"},
        ]
        path = s.export_fine_tune(traces, format="openai", min_score=0.8)
        lines = Path(path).read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2  # only score >= 0.8

    def test_export_no_qualifying_traces_raises(self, tmp_path):
        s = SkillSolidifier(vault_path=str(tmp_path))
        traces = [
            {"status": "success", "history": [{"output": "a", "score": 0.1}], "final_output": "A"},
        ]
        with pytest.raises(ValueError, match="没有通过评分阈值"):
            s.export_fine_tune(traces, format="openai", min_score=0.8)

    def test_export_unsupported_format_raises(self, tmp_path):
        s = SkillSolidifier(vault_path=str(tmp_path))
        with pytest.raises(ValueError, match="不支持的导出格式"):
            s.export_fine_tune(_traces(1), format="unknown")

    def test_export_custom_output_path(self, tmp_path):
        s = SkillSolidifier(vault_path=str(tmp_path / "vault"))
        out_dir = tmp_path / "custom_output"
        path = s.export_fine_tune(_traces(2), format="openai", output_path=str(out_dir))
        assert Path(path).exists()
        assert str(out_dir) in path

    def test_export_empty_traces_raises(self, tmp_path):
        s = SkillSolidifier(vault_path=str(tmp_path))
        with pytest.raises(ValueError):
            s.export_fine_tune([], format="openai")
