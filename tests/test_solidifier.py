"""Tests for core.solidifier — skill distillation and .trs export."""

import json
import pytest
from pathlib import Path

from core.solidifier import SkillSolidifier


@pytest.fixture
def vault_path(tmp_path):
    return str(tmp_path / "vault")


@pytest.fixture
def solidifier(vault_path):
    return SkillSolidifier(vault_path=vault_path)


def _traces(success_count=3, fail_count=1):
    traces = []
    for i in range(success_count):
        traces.append({
            "status": "success",
            "history": [
                {"iteration": 1, "output": f"Output {i}", "passed": True, "score": 0.9 + i * 0.02, "critique": None}
            ],
        })
    for i in range(fail_count):
        traces.append({
            "status": "exhausted",
            "history": [
                {"iteration": 1, "output": f"Bad {i}", "passed": False, "score": 0.2, "critique": "Too short"},
                {"iteration": 2, "output": f"Still bad {i}", "passed": False, "score": 0.3, "critique": "Not good"},
            ],
        })
    return traces


class TestSkillSolidifier:
    def test_distill_creates_file(self, solidifier, vault_path):
        traces = _traces()
        path = solidifier.distill("Test Task", traces, "Do {{ data }}")
        assert Path(path).exists()
        assert path.endswith(".trs")

    def test_distill_content_structure(self, solidifier):
        traces = _traces()
        path = solidifier.distill("Test Task", traces, "Do {{ data }}")
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        assert data["name"] == "Test Task"
        assert data["optimized_prompt"] == "Do {{ data }}"
        assert "skill_id" in data
        assert "performance_stats" in data
        assert "golden_samples" in data

    def test_distill_with_model_config(self, solidifier):
        traces = _traces()
        path = solidifier.distill("Test", traces, "Do {{ data }}", model_config={"model": "gpt-4o"})
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        assert data["model_config"]["model"] == "gpt-4o"

    def test_distill_with_validation_rules(self, solidifier):
        traces = _traces()
        rules = [{"type": "llm_eval", "criteria": "good"}]
        path = solidifier.distill("Test", traces, "Do {{ data }}", validation_rules=rules)
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        assert len(data["validation_rules"]) == 1

    def test_golden_samples_extraction(self, solidifier):
        traces = _traces(success_count=5)
        path = solidifier.distill("Test", traces, "Do {{ data }}")
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        # Should have up to 5 golden samples
        assert len(data["golden_samples"]) <= 5
        for sample in data["golden_samples"]:
            assert "output" in sample
            assert "score" in sample

    def test_performance_stats(self, solidifier):
        traces = _traces(success_count=3, fail_count=1)
        path = solidifier.distill("Test", traces, "Do {{ data }}")
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        stats = data["performance_stats"]
        assert stats["total"] == 4
        assert stats["success_rate"] == 0.75
        assert stats["average_retries"] > 0

    def test_empty_traces(self, solidifier):
        path = solidifier.distill("Test", [], "Do {{ data }}")
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        assert data["performance_stats"]["total"] == 0
        assert data["golden_samples"] == []

    def test_load_skill(self, solidifier):
        traces = _traces()
        path = solidifier.distill("Test", traces, "Do {{ data }}")
        skill_id = Path(path).stem
        loaded = solidifier.load_skill(skill_id)
        assert loaded["name"] == "Test"

    def test_load_skill_not_found(self, solidifier):
        with pytest.raises(FileNotFoundError):
            solidifier.load_skill("nonexistent")

    def test_list_skills(self, solidifier):
        solidifier.distill("Task A", _traces(), "A")
        solidifier.distill("Task B", _traces(), "B")
        skills = solidifier.list_skills()
        assert len(skills) == 2
