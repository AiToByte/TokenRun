"""Error recovery tests — verify graceful degradation and data integrity."""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from core.critic import TaskCritic
from core.ledger import BudgetExceededError, TokenLedger
from core.models import EvaluationResult, LoopConfig, TaskNode, ValidationRule
from core.persistence import TaskPersistence
from core.runner import ActorCriticLoop
from gateway.privacy import PrivacyRedactor
from gateway.provider import LLMProvider, LLMProviderError, LLMResponse


def _make_node():
    return TaskNode(
        id="n1", name="Test", actor_prompt_template="Do {{ data }}",
        loop_config=LoopConfig(max_attempts=3, exit_criteria=[
            ValidationRule(type="llm_eval", criteria="good")
        ]),
    )


class TestActorFailureRecovery:
    @pytest.mark.asyncio
    async def test_actor_exception_propagates(self):
        """Actor exceptions should propagate (not silently swallowed)."""
        from core.actor import TaskActor
        actor = MagicMock(spec=TaskActor)
        actor.generate = AsyncMock(side_effect=LLMProviderError("API down"))

        critic = MagicMock(spec=TaskCritic)
        critic.evaluate = AsyncMock(return_value=EvaluationResult(passed=True, score=0.9))
        critic.provider = MagicMock()
        critic.provider.model_name = "m"

        engine = ActorCriticLoop(actor=actor, critic=critic)

        with pytest.raises(LLMProviderError, match="API down"):
            await engine.run(_make_node(), "data")

    @pytest.mark.asyncio
    async def test_critic_failure_returns_degraded_result(self):
        """Critic JSON parse failure should return degraded result, not crash."""
        from core.actor import TaskActor
        actor = MagicMock(spec=TaskActor)
        actor.generate = AsyncMock(return_value=LLMResponse(
            content="output", prompt_tokens=10, completion_tokens=5, model_name="m"
        ))

        provider = MagicMock(spec=LLMProvider)
        provider.request = AsyncMock(return_value=LLMResponse(
            content="invalid json {{{", prompt_tokens=10, completion_tokens=5, model_name="m"
        ))
        provider.model_name = "m"
        critic = TaskCritic(provider=provider)

        engine = ActorCriticLoop(actor=actor, critic=critic)
        result = await engine.run(_make_node(), "data")

        # Should exhaust all attempts (degraded result always fails)
        assert result["status"] == "exhausted"
        assert all(h["passed"] is False for h in result["history"])

    @pytest.mark.asyncio
    async def test_budget_exhausted_during_loop(self):
        """Budget fuse during loop should be caught by orchestrator."""
        from core.actor import TaskActor
        actor = MagicMock(spec=TaskActor)
        actor.generate = AsyncMock(return_value=LLMResponse(
            content="output", prompt_tokens=10, completion_tokens=5, model_name="m"
        ))
        critic = MagicMock(spec=TaskCritic)
        critic.evaluate = AsyncMock(return_value=EvaluationResult(passed=False, score=0.3, audit_cost=5))
        critic.provider = MagicMock()
        critic.provider.model_name = "m"

        pricing = {"m": {"prompt": 100.0, "completion": 100.0}}
        ledger = TokenLedger(budget_usd=0.001, pricing_map=pricing)
        engine = ActorCriticLoop(actor=actor, critic=critic, ledger=ledger)

        with pytest.raises(BudgetExceededError):
            await engine.run(_make_node(), "data")


class TestPersistenceRecovery:
    def test_status_check_after_crash(self, tmp_path):
        """Status should survive process restart (SQLite persistence)."""
        db_path = str(tmp_path / "crash.db")
        p1 = TaskPersistence(db_path=db_path)
        p1.save_trace("unit-1", "h1", "completed", {"data": "test"}, "output")

        # Simulate restart
        p2 = TaskPersistence(db_path=db_path)
        assert p2.get_status("unit-1") == "completed"

    def test_partial_write_idempotent(self, tmp_path):
        """Re-saving the same unit should overwrite, not duplicate."""
        p = TaskPersistence(db_path=str(tmp_path / "idem.db"))
        p.save_trace("u1", "h1", "running", {"step": 1})
        p.save_trace("u1", "h1", "completed", {"step": 2}, "final")
        p.save_trace("u1", "h1", "completed", {"step": 3}, "updated")

        traces = p.get_all_traces()
        assert len(traces) == 1
        # The final_output should be from the last save
        import json
        assert traces[0]["final_output"] == "updated"


class TestPrivacyRecovery:
    def test_clear_vault_and_reuse(self):
        """After clearing vault, redactor should work fresh."""
        r = PrivacyRedactor()
        masked1 = r.mask("alice@test.com")
        assert "[[TR_EMAIL_1]]" in masked1

        r.clear_vault()
        masked2 = r.mask("bob@test.com")
        # After clear, counter resets, so new email gets _1 again
        assert "[[TR_EMAIL_1]]" in masked2
        assert r.vault_size == 1

    def test_unmask_after_multiple_operations(self):
        """Unmask should work correctly after many mask operations."""
        r = PrivacyRedactor()
        originals = [f"user{i}@test.com" for i in range(20)]
        masked_list = [r.mask(o) for o in originals]

        for original, masked in zip(originals, masked_list):
            restored = r.unmask(masked)
            assert restored == original


class TestLedgerRecovery:
    def test_summary_after_fuse(self):
        """Summary should be accurate even after fuse."""
        pricing = {"m": {"prompt": 1000.0, "completion": 1000.0}}
        ledger = TokenLedger(budget_usd=0.001, pricing_map=pricing)

        try:
            ledger.record_usage("m", prompt_tokens=10, completion_tokens=10)
        except BudgetExceededError:
            pass

        assert ledger.is_fused is True
        summary = ledger.get_summary()
        assert "total_cost" in summary
