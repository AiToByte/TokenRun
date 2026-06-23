"""Edge case tests for TokenRun — covering audit gaps."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from core.actor import TaskActor
from core.critic import TaskCritic
from core.drift_detector import DriftDetector
from core.ledger import BudgetExceededError, TokenLedger
from core.models import (
    EvaluationResult,
    LoopConfig,
    LoopStrategy,
    Runfile,
    TaskNode,
    ValidationRule,
)
from core.orchestrator import TROrchestrator
from core.runner import ActorCriticLoop
from core.sampling_manager import SamplingManager
from gateway.provider import LLMResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _actor_response(content="OK"):
    return LLMResponse(content=content, prompt_tokens=10, completion_tokens=5, model_name="m")


def _critic_response(passed=True, score=0.9, scores=None):
    return EvaluationResult(passed=passed, score=score, scores=scores or {"q": 0.9}, audit_cost=5)


def _make_node(strategy=LoopStrategy.FEEDBACK_DRIVEN, max_attempts=3):
    return TaskNode(
        id="n1", name="Test", actor_prompt_template="Do {{ data }}",
        loop_config=LoopConfig(
            strategy=strategy, max_attempts=max_attempts,
            exit_criteria=[ValidationRule(type="llm_eval", criteria="good")],
        ),
    )


# ---------------------------------------------------------------------------
# #13: EXHAUSTIVE with all failed (best_result is None)
# ---------------------------------------------------------------------------

class TestExhaustiveEdgeCases:
    @pytest.mark.asyncio
    async def test_exhaustive_all_fail_best_result_none(self):
        """EXHAUSTIVE mode where no iteration has scores > 0."""
        actor = MagicMock(spec=TaskActor)
        actor.generate = AsyncMock(return_value=_actor_response("Bad"))

        critic = MagicMock(spec=TaskCritic)
        critic.evaluate = AsyncMock(return_value=_critic_response(passed=False, score=0.0, scores={"q": 0.0}))
        critic.provider = MagicMock()
        critic.provider.model_name = "m"

        engine = ActorCriticLoop(actor=actor, critic=critic)
        node = _make_node(strategy=LoopStrategy.EXHAUSTIVE, max_attempts=2)
        result = await engine.run(node, "data")

        assert result["status"] == "exhausted"
        assert len(result["history"]) == 2


# ---------------------------------------------------------------------------
# #14: BudgetExceededError mid-loop
# ---------------------------------------------------------------------------

class TestBudgetMidLoop:
    @pytest.mark.asyncio
    async def test_orchestrator_catches_budget_exceeded(self):
        """Orchestrator should catch BudgetExceededError and return budget_exceeded."""
        node = _make_node()
        runfile = Runfile(workflow=[node])

        actor = MagicMock(spec=TaskActor)
        actor.generate = AsyncMock(return_value=_actor_response("OK"))

        critic = MagicMock(spec=TaskCritic)
        critic.evaluate = AsyncMock(return_value=_critic_response(passed=True))
        critic.provider = MagicMock()
        critic.provider.model_name = "m"

        # Ledger with tiny budget that will trip on the first call
        pricing = {"m": {"prompt": 100.0, "completion": 100.0}}
        ledger = TokenLedger(budget_usd=0.001, pricing_map=pricing)

        engine = ActorCriticLoop(actor=actor, critic=critic, ledger=ledger)
        orch = TROrchestrator(runfile=runfile, loop_engine=engine, ledger=ledger)

        results = await orch.run_mass_production(["data1"])
        assert len(results) == 1
        assert results[0]["status"] == "budget_exceeded"


# ---------------------------------------------------------------------------
# #15: DAG cyclic dependency
# ---------------------------------------------------------------------------

class TestDAGCyclic:
    def test_topological_sort_cyclic_raises(self):
        node_a = TaskNode(id="a", name="A", depends_on=["b"], actor_prompt_template="x")
        node_b = TaskNode(id="b", name="B", depends_on=["a"], actor_prompt_template="y")

        with pytest.raises(ValueError, match="循环依赖"):
            TROrchestrator._topological_sort([node_a, node_b])


# ---------------------------------------------------------------------------
# #16: DriftDetector empty golden samples
# ---------------------------------------------------------------------------

class TestDriftDetectorEdge:
    @pytest.mark.asyncio
    async def test_empty_golden_samples_returns_immediately(self):
        actor = MagicMock(spec=TaskActor)
        dd = DriftDetector(actor=actor, golden_samples=[], check_interval=1)
        dd.tick()
        report = await dd.run_check("template")
        assert report["drift_detected"] is False
        assert report["match_rate"] == 1.0

    @pytest.mark.asyncio
    async def test_empty_hash_always_matches(self):
        """expected_output_hash='' means no comparison, always matches."""
        actor = MagicMock(spec=TaskActor)
        provider = MagicMock()
        provider.request = AsyncMock(return_value=_actor_response("anything"))
        actor.provider = provider

        dd = DriftDetector(
            actor=actor,
            golden_samples=[{"input": "test", "expected_output_hash": ""}],
            check_interval=1, threshold=1.0,
        )
        dd.tick()
        report = await dd.run_check("template")
        assert report["drift_detected"] is False
        assert report["match_rate"] == 1.0


# ---------------------------------------------------------------------------
# #17: SamplingManager empty results
# ---------------------------------------------------------------------------

class TestSamplingManagerEdge:
    @pytest.mark.asyncio
    async def test_empty_results_no_division_error(self):
        sm = SamplingManager()
        report = await sm.generate_report([])
        assert report["summary"]["sample_count"] == 0
        assert report["summary"]["success_rate"] == 0.0
        assert report["economics"]["estimated_success_count"] == 0


# ---------------------------------------------------------------------------
# #18: PrivacyRedactor plural alias mismatch
# ---------------------------------------------------------------------------

class TestPrivacyAliasEdge:
    def test_phones_alias_does_not_match_PHONE(self):
        """Plural 'phones' is not aliased — should not mask phone numbers."""
        from gateway.privacy import PrivacyRedactor
        r = PrivacyRedactor(rules=["phones"])
        masked = r.mask("Call 13800138000")
        # "PHONES" is not in the alias map, so PHONE pattern is not active
        assert "13800138000" in masked  # NOT masked

    def test_api_keys_alias_works(self):
        """api_keys → API_KEY alias should work."""
        from gateway.privacy import PrivacyRedactor
        r = PrivacyRedactor(rules=["api_keys"])
        masked = r.mask("Key: sk-abcdefghijklmnopqrstuvwx")
        assert "sk-abcdefghijklmnopqrstuvwx" not in masked


# ---------------------------------------------------------------------------
# #19: Orchestrator count mode sampling
# ---------------------------------------------------------------------------

class TestOrchestratorCountMode:
    @pytest.mark.asyncio
    async def test_count_mode_sampling(self):
        node = _make_node()
        runfile = Runfile(workflow=[node])
        runfile.sampling.mode = "count"
        runfile.sampling.value = 2

        actor = MagicMock(spec=TaskActor)
        actor.generate = AsyncMock(return_value=_actor_response("OK"))
        critic = MagicMock(spec=TaskCritic)
        critic.evaluate = AsyncMock(return_value=_critic_response(passed=True))
        critic.provider = MagicMock()
        critic.provider.model_name = "m"

        engine = ActorCriticLoop(actor=actor, critic=critic)
        ledger = TokenLedger(budget_usd=10.0)
        orch = TROrchestrator(runfile=runfile, loop_engine=engine, ledger=ledger)

        results = await orch.run_sampling_gate(["a", "b", "c", "d", "e"])
        # Should process exactly 2 items (count mode)
        assert actor.generate.await_count == 2


# ---------------------------------------------------------------------------
# Bonus: Critic JSON parse failure audit_cost
# ---------------------------------------------------------------------------

class TestCriticEdge:
    @pytest.mark.asyncio
    async def test_json_parse_failure_still_records_audit_cost(self):
        """Even when LLM returns invalid JSON, audit_cost should be set."""
        from gateway.provider import LLMProvider

        provider = MagicMock(spec=LLMProvider)
        provider.request = AsyncMock(return_value=LLMResponse(
            content="this is not json", prompt_tokens=10, completion_tokens=5, model_name="m"
        ))
        provider.model_name = "m"

        critic = TaskCritic(provider=provider)
        result = await critic.evaluate(
            "test", "input", "output",
            [ValidationRule(type="llm_eval", criteria="good")]
        )

        assert result.passed is False
        assert result.audit_cost == 15  # 10 + 5
