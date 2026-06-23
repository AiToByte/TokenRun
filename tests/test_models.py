"""Tests for core.models — Runfile parsing and validation."""

import pytest
import yaml

from core.models import (
    DeterminismLevel,
    EvaluationResult,
    ExecutionIteration,
    Fingerprint,
    GovernanceConfig,
    LoopConfig,
    LoopStrategy,
    PromptVersion,
    Resource,
    ResourceType,
    Runfile,
    SamplingConfig,
    SecurityConfig,
    TaskNode,
    TaskStatus,
    TaskTrace,
    ValidationRule,
)


# ---------------------------------------------------------------------------
# Enum tests
# ---------------------------------------------------------------------------

class TestEnums:
    def test_determinism_level_values(self):
        assert DeterminismLevel.STRICT == "strict"
        assert DeterminismLevel.FLEXIBLE == "flexible"

    def test_loop_strategy_values(self):
        assert LoopStrategy.FEEDBACK_DRIVEN == "feedback-driven"
        assert LoopStrategy.EXHAUSTIVE == "exhaustive"
        assert LoopStrategy.ONCE == "once"

    def test_resource_type_values(self):
        assert ResourceType.LOCAL_FILE == "local_file"
        assert ResourceType.SQL_QUERY == "sql_query"

    def test_task_status_values(self):
        assert TaskStatus.PENDING == "pending"
        assert TaskStatus.COMPLETED == "completed"
        assert TaskStatus.FAILED == "failed"


# ---------------------------------------------------------------------------
# Model construction tests
# ---------------------------------------------------------------------------

class TestModels:
    def test_resource_defaults(self):
        r = Resource(id="r1", uri="local://./data", type=ResourceType.LOCAL_FILE)
        assert r.id == "r1"
        assert r.description is None

    def test_security_config_defaults(self):
        s = SecurityConfig()
        assert "emails" in s.masking_rules
        assert s.local_sandbox is True

    def test_sampling_config_defaults(self):
        s = SamplingConfig()
        assert s.enabled is True
        assert s.value == 0.01
        assert s.auto_pause is True

    def test_fingerprint_with_parameters(self):
        fp = Fingerprint(
            model_id="gpt-4o",
            prompt_hash="abc123",
            parameters={"temperature": 0.1, "top_p": 1.0},
        )
        assert fp.parameters["temperature"] == 0.1

    def test_validation_rule(self):
        rule = ValidationRule(type="regex", criteria=r"\d+", weight=0.8)
        assert rule.type == "regex"
        assert rule.weight == 0.8

    def test_loop_config_defaults(self):
        lc = LoopConfig()
        assert lc.strategy == LoopStrategy.FEEDBACK_DRIVEN
        assert lc.max_attempts == 3
        assert lc.retry_delay == 1

    def test_task_node_minimal(self):
        node = TaskNode(
            id="t1",
            name="Test",
            actor_prompt_template="Hello {{ data }}",
        )
        assert node.id == "t1"
        assert node.depends_on == []
        assert node.loop_config.max_attempts == 3

    def test_prompt_version(self):
        pv = PromptVersion(
            version_id="v1",
            hash="abc",
            template="Hello",
            parent_id=None,
        )
        assert pv.version_id == "v1"

    def test_governance_config_defaults(self):
        g = GovernanceConfig()
        assert g.max_usd == 10.0
        assert g.max_loop_count is None


# ---------------------------------------------------------------------------
# Runfile construction
# ---------------------------------------------------------------------------

class TestRunfile:
    def _make_minimal_runfile_data(self):
        return {
            "name": "Test",
            "workflow": [
                {
                    "id": "task1",
                    "name": "Task 1",
                    "actor_prompt_template": "Do {{ data }}",
                    "loop_config": {
                        "max_attempts": 2,
                        "exit_criteria": [
                            {"type": "llm_eval", "criteria": "Must be good"}
                        ],
                    },
                }
            ],
        }

    def test_runfile_from_dict(self):
        data = self._make_minimal_runfile_data()
        rf = Runfile(**data)
        assert rf.name == "Test"
        assert len(rf.workflow) == 1
        assert rf.workflow[0].id == "task1"
        assert rf.version == "1.0"

    def test_runfile_defaults(self):
        rf = Runfile()
        assert rf.name == "Unnamed Task"
        assert rf.workflow == []
        assert rf.security.local_sandbox is True
        assert rf.governance.max_usd == 10.0

    def test_runfile_with_resources(self):
        rf = Runfile(
            name="WithRes",
            context=[
                Resource(id="csv", uri="local://./data.csv", type=ResourceType.LOCAL_FILE)
            ],
            workflow=[],
        )
        assert len(rf.context) == 1
        assert rf.context[0].uri == "local://./data.csv"


# ---------------------------------------------------------------------------
# Execution trace tests
# ---------------------------------------------------------------------------

class TestTraces:
    def test_evaluation_result_defaults(self):
        er = EvaluationResult()
        assert er.passed is False
        assert er.score == 0.0
        assert er.suggestions == []

    def test_evaluation_result_passed(self):
        er = EvaluationResult(passed=True, score=0.95, critique=None)
        assert er.passed is True
        assert er.score == 0.95

    def test_execution_iteration(self):
        ei = ExecutionIteration(
            iteration_index=1,
            input_payload="hello",
            output_content="world",
            evaluation=EvaluationResult(passed=True, score=1.0),
            latency_ms=150,
        )
        assert ei.iteration_index == 1
        assert ei.evaluation.passed is True

    def test_task_trace_default(self):
        t = TaskTrace(task_id="t1")
        assert t.status == TaskStatus.PENDING
        assert t.iterations == []
        assert t.final_output is None

    def test_task_trace_completed(self):
        t = TaskTrace(
            task_id="t1",
            status=TaskStatus.COMPLETED,
            iterations=[
                ExecutionIteration(
                    iteration_index=1,
                    input_payload="x",
                    output_content="y",
                    evaluation=EvaluationResult(passed=True, score=1.0),
                )
            ],
            final_output="y",
        )
        assert t.status == TaskStatus.COMPLETED
        assert len(t.iterations) == 1


# ---------------------------------------------------------------------------
# YAML round-trip
# ---------------------------------------------------------------------------

class TestYAMLRoundTrip:
    def test_parse_test_mission_yaml(self):
        """Verify the bundled test_mission.yaml parses into a Runfile."""
        with open("runfiles/test_mission.yaml", "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        rf = Runfile(**data)
        assert rf.name == "Simple_Refinery_Test"
        assert len(rf.workflow) == 1
        assert rf.workflow[0].id == "summarizer"
        assert rf.workflow[0].loop_config.max_attempts == 3
        assert rf.governance.max_usd == 1.0
