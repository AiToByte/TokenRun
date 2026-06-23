"""Security tests — verify PII protection, budget enforcement, and injection safety."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from core.actor import TaskActor
from core.critic import TaskCritic
from core.ledger import BudgetExceededError, TokenLedger
from core.models import (
    EvaluationResult,
    LoopConfig,
    TaskNode,
    ValidationRule,
)
from core.runner import ActorCriticLoop
from gateway.privacy import PrivacyRedactor
from gateway.provider import LLMResponse


class TestPIIProtection:
    @pytest.mark.asyncio
    async def test_actor_never_sees_real_pii(self):
        """Actor should receive masked input, never real PII."""
        received = []
        actor = MagicMock(spec=TaskActor)

        async def capture(**kwargs):
            received.append(kwargs.get("data", ""))
            return LLMResponse(content="output", prompt_tokens=10, completion_tokens=5, model_name="m")
        actor.generate = AsyncMock(side_effect=capture)

        critic = MagicMock(spec=TaskCritic)
        critic.evaluate = AsyncMock(return_value=EvaluationResult(passed=True, score=0.9, audit_cost=5))
        critic.provider = MagicMock()
        critic.provider.model_name = "m"

        redactor = PrivacyRedactor()
        engine = ActorCriticLoop(actor=actor, critic=critic, redactor=redactor)

        node = TaskNode(
            id="n1", name="T", actor_prompt_template="{{ data }}",
            loop_config=LoopConfig(max_attempts=1, exit_criteria=[
                ValidationRule(type="llm_eval", criteria="good")
            ]),
        )
        await engine.run(node, "Contact alice@test.com or call 13800138000")

        # Actor should see placeholders, not real values
        actor_input = received[0]
        assert "alice@test.com" not in actor_input
        assert "13800138000" not in actor_input
        assert "[[TR_EMAIL_" in actor_input
        assert "[[TR_PHONE_" in actor_input

    @pytest.mark.asyncio
    async def test_critic_never_sees_real_pii(self):
        """Critic should also receive masked input."""
        received = []
        actor = MagicMock(spec=TaskActor)
        actor.generate = AsyncMock(return_value=LLMResponse(
            content="Output with alice@test.com", prompt_tokens=10, completion_tokens=5, model_name="m"
        ))

        critic = MagicMock(spec=TaskCritic)

        async def capture(**kwargs):
            received.append(kwargs.get("input_data", ""))
            return EvaluationResult(passed=True, score=0.9, audit_cost=5)
        critic.evaluate = AsyncMock(side_effect=capture)
        critic.provider = MagicMock()
        critic.provider.model_name = "m"

        redactor = PrivacyRedactor()
        engine = ActorCriticLoop(actor=actor, critic=critic, redactor=redactor)

        node = TaskNode(
            id="n1", name="T", actor_prompt_template="{{ data }}",
            loop_config=LoopConfig(max_attempts=1, exit_criteria=[
                ValidationRule(type="llm_eval", criteria="good")
            ]),
        )
        await engine.run(node, "Email: alice@test.com")

        # Critic should also see masked input
        critic_input = received[0]
        assert "alice@test.com" not in critic_input
        assert "[[TR_EMAIL_" in critic_input

    @pytest.mark.asyncio
    async def test_final_output_unmasked(self):
        """Final output should contain real values (unmasked)."""
        actor = MagicMock(spec=TaskActor)
        actor.generate = AsyncMock(return_value=LLMResponse(
            content="[[TR_EMAIL_1]] is the contact", prompt_tokens=10, completion_tokens=5, model_name="m"
        ))
        critic = MagicMock(spec=TaskCritic)
        critic.evaluate = AsyncMock(return_value=EvaluationResult(passed=True, score=0.9, audit_cost=5))
        critic.provider = MagicMock()
        critic.provider.model_name = "m"

        redactor = PrivacyRedactor()
        engine = ActorCriticLoop(actor=actor, critic=critic, redactor=redactor)

        node = TaskNode(
            id="n1", name="T", actor_prompt_template="{{ data }}",
            loop_config=LoopConfig(max_attempts=1, exit_criteria=[
                ValidationRule(type="llm_eval", criteria="good")
            ]),
        )
        result = await engine.run(node, "Email: alice@test.com")

        # Final output should be unmasked
        assert "alice@test.com" in result["final_output"]
        assert "[[TR_EMAIL_" not in result["final_output"]


class TestBudgetEnforcement:
    @pytest.mark.asyncio
    async def test_budget_cannot_be_bypassed(self):
        """Ledger should enforce budget even with rapid calls."""
        pricing = {"m": {"prompt": 100.0, "completion": 100.0}}
        ledger = TokenLedger(budget_usd=0.01, pricing_map=pricing)

        # First call should trigger fuse
        with pytest.raises(BudgetExceededError):
            ledger.record_usage("m", prompt_tokens=1, completion_tokens=1)

        # Subsequent calls should be recorded but not raise again
        ledger.record_usage("m", prompt_tokens=10000, completion_tokens=10000)
        assert ledger.report.call_count == 2  # both counted
        assert ledger.is_fused is True

    @pytest.mark.asyncio
    async def test_unknown_model_not_zero_cost(self):
        """Unknown models should use fallback pricing, not zero."""
        ledger = TokenLedger(budget_usd=10.0)
        ledger.record_usage("unknown-model", prompt_tokens=1000, completion_tokens=1000)
        assert ledger.report.total_cost_usd > 0


class TestInjectionSafety:
    def test_runfile_rejects_extra_fields(self):
        """Runfile should reject unknown fields (extra=forbid)."""
        import pydantic
        from core.models import Runfile
        with pytest.raises(pydantic.ValidationError):
            Runfile(name="test", malicious_field="payload")

    def test_regex_pattern_safety(self):
        """Regex patterns should not cause catastrophic backtracking."""
        import time
        from core.runner import ActorCriticLoop
        from core.models import ValidationRule

        # A potentially slow regex
        rule = ValidationRule(type="regex", criteria=r"(a+)+b")
        start = time.time()
        passed, _ = ActorCriticLoop._run_programmatic_rules([rule], "a" * 30 + "b")
        elapsed = time.time() - start
        # Should complete in reasonable time (< 1 second)
        assert elapsed < 1.0
