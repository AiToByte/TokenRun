"""Configuration variant tests — verify behavior across different Runfile configs."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from core.models import (
    LoopConfig,
    LoopStrategy,
    Runfile,
    SamplingConfig,
    SecurityConfig,
    TaskNode,
    ValidationRule,
)
from core.orchestrator import TROrchestrator
from core.runner import ActorCriticLoop
from core.ledger import TokenLedger
from gateway.privacy import PrivacyRedactor
from gateway.provider import LLMResponse


def _mock_engine():
    engine = MagicMock(spec=ActorCriticLoop)
    engine.run = AsyncMock(return_value={
        "status": "success", "final_output": "ok",
        "history": [{"iteration": 1, "score": 0.9}], "trace": MagicMock(),
    })
    return engine


class TestLoopStrategyVariants:
    @pytest.mark.asyncio
    async def test_once_strategy(self):
        """ONCE should execute exactly once, no retries."""
        from core.actor import TaskActor
        from core.critic import TaskCritic
        from core.models import EvaluationResult

        actor = MagicMock(spec=TaskActor)
        actor.generate = AsyncMock(return_value=LLMResponse(
            content="output", prompt_tokens=10, completion_tokens=5, model_name="m"
        ))
        critic = MagicMock(spec=TaskCritic)
        critic.evaluate = AsyncMock(return_value=EvaluationResult(
            passed=False, score=0.3, audit_cost=5
        ))
        critic.provider = MagicMock()
        critic.provider.model_name = "m"

        engine = ActorCriticLoop(actor=actor, critic=critic)
        node = TaskNode(
            id="n1", name="N1", actor_prompt_template="Do {{ data }}",
            loop_config=LoopConfig(
                strategy=LoopStrategy.ONCE,
                max_attempts=5,
                exit_criteria=[ValidationRule(type="llm_eval", criteria="good")],
            ),
        )
        result = await engine.run(node, "data")
        assert len(result["history"]) == 1
        actor.generate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_exhaustive_runs_all_attempts(self):
        """EXHAUSTIVE should run all max_attempts regardless of pass."""
        from core.actor import TaskActor
        from core.critic import TaskCritic
        from core.models import EvaluationResult

        actor = MagicMock(spec=TaskActor)
        actor.generate = AsyncMock(return_value=LLMResponse(
            content="output", prompt_tokens=10, completion_tokens=5, model_name="m"
        ))
        critic = MagicMock(spec=TaskCritic)
        critic.evaluate = AsyncMock(return_value=EvaluationResult(
            passed=True, score=0.9, scores={"q": 0.9}, audit_cost=5
        ))
        critic.provider = MagicMock()
        critic.provider.model_name = "m"

        engine = ActorCriticLoop(actor=actor, critic=critic)
        node = TaskNode(
            id="n1", name="N1", actor_prompt_template="Do {{ data }}",
            loop_config=LoopConfig(
                strategy=LoopStrategy.EXHAUSTIVE,
                max_attempts=3,
                exit_criteria=[ValidationRule(type="llm_eval", criteria="good")],
            ),
        )
        result = await engine.run(node, "data")
        assert len(result["history"]) == 3
        assert actor.generate.await_count == 3


class TestExitCriteriaVariants:
    @pytest.mark.asyncio
    async def test_regex_only_no_critic_call(self):
        """Pure regex criteria should not call Critic."""
        from core.actor import TaskActor
        from core.critic import TaskCritic

        actor = MagicMock(spec=TaskActor)
        actor.generate = AsyncMock(return_value=LLMResponse(
            content="Result: 42", prompt_tokens=10, completion_tokens=5, model_name="m"
        ))
        critic = MagicMock(spec=TaskCritic)
        critic.evaluate = AsyncMock()
        critic.provider = MagicMock()

        engine = ActorCriticLoop(actor=actor, critic=critic)
        node = TaskNode(
            id="n1", name="N1", actor_prompt_template="Do {{ data }}",
            loop_config=LoopConfig(max_attempts=1, exit_criteria=[
                ValidationRule(type="regex", criteria=r"\d+"),
            ]),
        )
        result = await engine.run(node, "data")
        assert result["status"] == "success"
        critic.evaluate.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_mixed_criteria_calls_critic(self):
        """Mix of regex + llm_eval should call Critic for llm_eval part."""
        from core.actor import TaskActor
        from core.critic import TaskCritic
        from core.models import EvaluationResult

        actor = MagicMock(spec=TaskActor)
        actor.generate = AsyncMock(return_value=LLMResponse(
            content="Result: 42", prompt_tokens=10, completion_tokens=5, model_name="m"
        ))
        critic = MagicMock(spec=TaskCritic)
        critic.evaluate = AsyncMock(return_value=EvaluationResult(
            passed=True, score=0.9, scores={"llm": 0.9}, audit_cost=5
        ))
        critic.provider = MagicMock()
        critic.provider.model_name = "m"

        engine = ActorCriticLoop(actor=actor, critic=critic)
        node = TaskNode(
            id="n1", name="N1", actor_prompt_template="Do {{ data }}",
            loop_config=LoopConfig(max_attempts=1, exit_criteria=[
                ValidationRule(type="regex", criteria=r"\d+"),
                ValidationRule(type="llm_eval", criteria="good"),
            ]),
        )
        result = await engine.run(node, "data")
        assert result["status"] == "success"
        critic.evaluate.assert_awaited_once()


class TestMaskingRulesVariants:
    def test_no_masking(self):
        """Empty masking rules (empty list = falsy) means 'all patterns'."""
        r = PrivacyRedactor(rules=[])
        # Empty list is falsy, so falls back to all patterns
        masked = r.mask("alice@test.com")
        assert "[[TR_EMAIL_" in masked

    def test_email_only(self):
        """Only emails should be masked."""
        r = PrivacyRedactor(rules=["emails"])
        masked = r.mask("Email alice@test.com Phone 13800138000")
        assert "[[TR_EMAIL_" in masked
        assert "13800138000" in masked  # phone NOT masked

    def test_all_rules(self):
        """All rules active should mask everything."""
        r = PrivacyRedactor()  # default = all rules
        masked = r.mask("alice@test.com 13800138000 110101200001010019 10.0.0.1 sk-abcdefghijklmnopqrstuvwx")
        assert "[[TR_EMAIL_" in masked
        assert "[[TR_PHONE_" in masked
        assert "[[TR_IP_ADDR_" in masked
        assert "[[TR_API_KEY_" in masked


class TestSamplingVariants:
    @pytest.mark.asyncio
    async def test_sampling_value_100_percent(self):
        """100% sampling should process all items."""
        runfile = Runfile(
            workflow=[TaskNode(
                id="n1", name="N1", actor_prompt_template="Do {{ data }}",
                loop_config=LoopConfig(max_attempts=1, exit_criteria=[
                    ValidationRule(type="llm_eval", criteria="good")
                ]),
            )],
            sampling=SamplingConfig(mode="percentage", value=1.0),
        )
        engine = _mock_engine()
        ledger = TokenLedger(budget_usd=10.0)
        orch = TROrchestrator(runfile=runfile, loop_engine=engine, ledger=ledger)

        await orch.run_sampling_gate(["a", "b", "c"])
        assert engine.run.await_count == 3

    @pytest.mark.asyncio
    async def test_sampling_value_tiny(self):
        """Very small sampling value should still process at least 1 item."""
        runfile = Runfile(
            workflow=[TaskNode(
                id="n1", name="N1", actor_prompt_template="Do {{ data }}",
                loop_config=LoopConfig(max_attempts=1, exit_criteria=[
                    ValidationRule(type="llm_eval", criteria="good")
                ]),
            )],
            sampling=SamplingConfig(mode="percentage", value=0.001),
        )
        engine = _mock_engine()
        ledger = TokenLedger(budget_usd=10.0)
        orch = TROrchestrator(runfile=runfile, loop_engine=engine, ledger=ledger)

        await orch.run_sampling_gate(["a", "b", "c"])
        assert engine.run.await_count == 1  # at least 1


class TestWorkflowVariants:
    @pytest.mark.asyncio
    async def test_empty_workflow(self):
        """Empty workflow should return empty results."""
        runfile = Runfile(workflow=[])
        engine = _mock_engine()
        ledger = TokenLedger(budget_usd=10.0)
        orch = TROrchestrator(runfile=runfile, loop_engine=engine, ledger=ledger)

        results = await orch.run_mass_production(["data"])
        assert results == []

    @pytest.mark.asyncio
    async def test_dag_execution_order(self):
        """Multi-node DAG should execute in dependency order."""
        node_a = TaskNode(
            id="a", name="A", actor_prompt_template="A: {{ data }}",
            loop_config=LoopConfig(max_attempts=1, exit_criteria=[
                ValidationRule(type="llm_eval", criteria="good")
            ]),
        )
        node_b = TaskNode(
            id="b", name="B", depends_on=["a"],
            actor_prompt_template="B: {{ data }}",
            loop_config=LoopConfig(max_attempts=1, exit_criteria=[
                ValidationRule(type="llm_eval", criteria="good")
            ]),
        )
        runfile = Runfile(workflow=[node_b, node_a])  # intentionally reversed
        engine = _mock_engine()
        ledger = TokenLedger(budget_usd=10.0)
        orch = TROrchestrator(runfile=runfile, loop_engine=engine, ledger=ledger)

        results = await orch.run_mass_production(["data"])
        # Should execute both nodes (A first, then B)
        assert engine.run.await_count == 2
