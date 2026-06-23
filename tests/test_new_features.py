"""Tests for new features: programmatic validation, EXHAUSTIVE, multi-dim scoring."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from core.actor import TaskActor
from core.critic import TaskCritic
from core.ledger import TokenLedger
from core.models import (
    EvaluationResult,
    LoopConfig,
    LoopStrategy,
    TaskNode,
    ValidationRule,
)
from core.runner import ActorCriticLoop
from core.telemetry import TelemetryManager, TelemetryEvent
from core.sampling_manager import SamplingManager
from gateway.provider import LLMResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _actor_response(content: str) -> LLMResponse:
    return LLMResponse(
        content=content,
        prompt_tokens=100,
        completion_tokens=50,
        model_name="test-actor",
    )


def _critic_response(passed=True, score=0.9, scores=None, critique=None):
    return EvaluationResult(
        passed=passed,
        score=score,
        scores=scores or {"accuracy": 0.9, "completeness": 0.85},
        critique=critique,
        audit_cost=20,
    )


def _make_node(rules=None, strategy=LoopStrategy.FEEDBACK_DRIVEN,
               max_attempts=3, score_weights=None, min_score=0.85):
    return TaskNode(
        id="test_node",
        name="Test Task",
        actor_prompt_template="Do {{ data }}",
        loop_config=LoopConfig(
            strategy=strategy,
            max_attempts=max_attempts,
            exit_criteria=rules or [
                ValidationRule(type="llm_eval", criteria="Must be good", weight=1.0)
            ],
            score_weights=score_weights or {},
            min_score=min_score,
        ),
    )


# ---------------------------------------------------------------------------
# Programmatic Validation Tests
# ---------------------------------------------------------------------------

class TestProgrammaticValidation:
    def test_split_rules_regex_and_llm(self):
        rules = [
            ValidationRule(type="regex", criteria=r"\d+"),
            ValidationRule(type="json_schema", criteria={"required": ["a"]}),
            ValidationRule(type="llm_eval", criteria="good"),
        ]
        prog, llm = ActorCriticLoop._split_rules(rules)
        assert len(prog) == 2
        assert len(llm) == 1
        assert prog[0].type == "regex"
        assert prog[1].type == "json_schema"
        assert llm[0].type == "llm_eval"

    def test_regex_pass(self):
        rules = [ValidationRule(type="regex", criteria=r"\d+")]
        passed, scores = ActorCriticLoop._run_programmatic_rules(rules, "Result: 42")
        assert passed is True
        assert all(v == 1.0 for v in scores.values())

    def test_regex_fail(self):
        rules = [ValidationRule(type="regex", criteria=r"\d+")]
        passed, scores = ActorCriticLoop._run_programmatic_rules(rules, "No numbers here")
        assert passed is False
        assert all(v == 0.0 for v in scores.values())

    def test_json_schema_pass(self):
        rules = [ValidationRule(type="json_schema", criteria={"required": ["date", "amount"]})]
        passed, scores = ActorCriticLoop._run_programmatic_rules(
            rules, '{"date": "2024-01-01", "amount": 100}'
        )
        assert passed is True
        assert scores["json_schema"] == 1.0

    def test_json_schema_fail_missing_field(self):
        rules = [ValidationRule(type="json_schema", criteria={"required": ["date", "amount"]})]
        passed, scores = ActorCriticLoop._run_programmatic_rules(
            rules, '{"date": "2024-01-01"}'
        )
        assert passed is False
        assert scores["json_schema"] == 0.0

    def test_json_schema_fail_invalid_json(self):
        rules = [ValidationRule(type="json_schema", criteria={"required": ["a"]})]
        passed, scores = ActorCriticLoop._run_programmatic_rules(rules, "not json")
        assert passed is False
        assert scores["json_schema"] == 0.0

    def test_empty_rules_pass(self):
        passed, scores = ActorCriticLoop._run_programmatic_rules([], "anything")
        assert passed is True
        assert scores == {}

    @pytest.mark.asyncio
    async def test_regex_only_no_critic_call(self):
        """When only regex rules exist, Critic should not be called."""
        actor = MagicMock(spec=TaskActor)
        actor.generate = AsyncMock(return_value=_actor_response("Result: 42"))

        critic = MagicMock(spec=TaskCritic)
        critic.evaluate = AsyncMock()
        critic.provider = MagicMock()
        critic.provider.model_name = "test-critic"

        engine = ActorCriticLoop(actor=actor, critic=critic)
        node = _make_node(rules=[
            ValidationRule(type="regex", criteria=r"\d+"),
        ])
        result = await engine.run(node, "data")

        assert result["status"] == "success"
        critic.evaluate.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_regex_fail_triggers_retry(self):
        """Regex failure should trigger retry with feedback."""
        actor = MagicMock(spec=TaskActor)
        actor.generate = AsyncMock(
            side_effect=[_actor_response("no numbers"), _actor_response("Result: 42")]
        )

        critic = MagicMock(spec=TaskCritic)
        critic.evaluate = AsyncMock()
        critic.provider = MagicMock()
        critic.provider.model_name = "test-critic"

        engine = ActorCriticLoop(actor=actor, critic=critic)
        node = _make_node(rules=[
            ValidationRule(type="regex", criteria=r"\d+"),
        ], max_attempts=3)
        result = await engine.run(node, "data")

        assert result["status"] == "success"
        assert len(result["history"]) == 2


# ---------------------------------------------------------------------------
# EXHAUSTIVE Strategy Tests
# ---------------------------------------------------------------------------

class TestExhaustiveStrategy:
    @pytest.mark.asyncio
    async def test_exhaustive_runs_all_attempts(self):
        """EXHAUSTIVE should run all max_attempts even if one passes."""
        actor = MagicMock(spec=TaskActor)
        actor.generate = AsyncMock(
            side_effect=[
                _actor_response("Output A"),
                _actor_response("Output B"),
                _actor_response("Output C"),
            ]
        )

        # Critic: first pass with low score, second pass with high score
        critic = MagicMock(spec=TaskCritic)
        critic.evaluate = AsyncMock(
            side_effect=[
                _critic_response(passed=True, score=0.7, scores={"quality": 0.7}),
                _critic_response(passed=True, score=0.95, scores={"quality": 0.95}),
                _critic_response(passed=False, score=0.5, scores={"quality": 0.5}),
            ]
        )
        critic.provider = MagicMock()
        critic.provider.model_name = "test-critic"

        engine = ActorCriticLoop(actor=actor, critic=critic)
        node = _make_node(
            strategy=LoopStrategy.EXHAUSTIVE,
            max_attempts=3,
            score_weights={"quality": 1.0},
            min_score=0.0,  # don't filter by min_score
        )
        result = await engine.run(node, "data")

        # Should run all 3 attempts
        assert actor.generate.await_count == 3
        assert len(result["history"]) == 3
        # Best result should be Output B (score 0.95)
        assert result["final_output"] == "Output B"


# ---------------------------------------------------------------------------
# Multi-dimensional Scoring Tests
# ---------------------------------------------------------------------------

class TestMultiDimensionalScoring:
    def test_weighted_score_with_weights(self):
        scores = {"accuracy": 0.9, "completeness": 0.8}
        weights = {"accuracy": 2.0, "completeness": 1.0}
        result = ActorCriticLoop._compute_weighted_score(scores, weights)
        # (0.9*2.0 + 0.8*1.0) / (2.0+1.0) = 2.6/3.0 = 0.867
        assert abs(result - 0.867) < 0.01

    def test_weighted_score_no_weights(self):
        scores = {"accuracy": 0.9, "completeness": 0.8}
        result = ActorCriticLoop._compute_weighted_score(scores, {})
        # simple average: (0.9 + 0.8) / 2 = 0.85
        assert abs(result - 0.85) < 0.01

    def test_weighted_score_empty(self):
        assert ActorCriticLoop._compute_weighted_score({}, {}) == 0.0

    @pytest.mark.asyncio
    async def test_min_score_blocks_pass(self):
        """Even if Critic says passed=True, low weighted score should fail."""
        actor = MagicMock(spec=TaskActor)
        actor.generate = AsyncMock(return_value=_actor_response("Output"))

        critic = MagicMock(spec=TaskCritic)
        critic.evaluate = AsyncMock(
            return_value=_critic_response(
                passed=True, score=0.5, scores={"quality": 0.5}
            )
        )
        critic.provider = MagicMock()
        critic.provider.model_name = "test-critic"

        engine = ActorCriticLoop(actor=actor, critic=critic)
        node = _make_node(
            score_weights={"quality": 1.0},
            min_score=0.85,
            max_attempts=1,
        )
        result = await engine.run(node, "data")

        # Should be exhausted because 0.5 < 0.85
        assert result["status"] == "exhausted"


# ---------------------------------------------------------------------------
# TelemetryManager Tests
# ---------------------------------------------------------------------------

class TestTelemetryManager:
    def test_emit_calls_handlers(self):
        tm = TelemetryManager()
        events = []
        tm.on_event(lambda e: events.append(e))
        tm.emit("TEST", "task-1", {"key": "value"})
        assert len(events) == 1
        assert events[0].event_type == "TEST"
        assert events[0].data == {"key": "value"}

    def test_emit_step(self):
        tm = TelemetryManager()
        events = []
        tm.on_event(lambda e: events.append(e))
        tm.emit_step("t1", "node1", 1, True, 0.9, "output text")
        assert events[0].event_type == "TRACE_EVENT"
        assert events[0].data["node_id"] == "node1"
        assert events[0].data["passed"] is True

    def test_emit_status(self):
        tm = TelemetryManager()
        events = []
        tm.on_event(lambda e: events.append(e))
        tm.emit_status("t1", "SAMPLING", 0.5, 0.10)
        assert events[0].event_type == "STATUS_UPDATE"
        assert events[0].data["phase"] == "SAMPLING"

    def test_handler_error_does_not_crash(self):
        tm = TelemetryManager()
        tm.on_event(lambda e: 1 / 0)  # will raise
        # Should not raise
        tm.emit("TEST", "task-1")

    def test_get_log(self):
        tm = TelemetryManager()
        tm.emit("A", "t1")
        tm.emit("B", "t2")
        log = tm.get_log()
        assert len(log) == 2
        assert log[0].event_type == "A"
        assert log[1].event_type == "B"

    def test_clear_log(self):
        tm = TelemetryManager()
        tm.emit("A", "t1")
        tm.clear_log()
        assert len(tm.get_log()) == 0


# ---------------------------------------------------------------------------
# SamplingManager ROI Tests
# ---------------------------------------------------------------------------

class TestSamplingManagerROI:
    @pytest.mark.asyncio
    async def test_report_contains_economics(self):
        sm = SamplingManager()
        results = [
            {"status": "success", "history": [{"score": 0.9}]},
            {"status": "success", "history": [{"score": 0.8}]},
            {"status": "exhausted", "history": [{"score": 0.3}]},
        ]
        report = await sm.generate_report(
            results, total_data_count=300, sampling_ratio=0.01, current_cost_usd=0.15
        )
        econ = report["economics"]
        assert econ["sampling_cost_usd"] == 0.15
        assert econ["total_data_count"] == 300
        assert econ["estimated_success_count"] == 200  # 2/3 * 300
        assert econ["cost_per_sample"] > 0

    @pytest.mark.asyncio
    async def test_report_success_rate(self):
        sm = SamplingManager()
        results = [
            {"status": "success", "history": [{"score": 0.9}]},
            {"status": "exhausted", "history": [{"score": 0.3}]},
        ]
        report = await sm.generate_report(results)
        assert report["summary"]["success_rate"] == 0.5


# ---------------------------------------------------------------------------
# Ledger ROI Report Tests
# ---------------------------------------------------------------------------

class TestLedgerROI:
    def test_roi_report_basic(self):
        ledger = TokenLedger(budget_usd=10.0)
        ledger.record_usage("gpt-4o", prompt_tokens=1000, completion_tokens=500)
        report = ledger.get_roi_report(data_count=10, success_count=8, skill_id="TR-SKILL-abc")
        assert "Proof of Value" in report
        assert "10" in report
        assert "8" in report
        assert "TR-SKILL-abc" in report

    def test_roi_report_no_data(self):
        ledger = TokenLedger(budget_usd=10.0)
        report = ledger.get_roi_report()
        assert "Proof of Value" in report
