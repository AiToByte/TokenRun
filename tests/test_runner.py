"""Tests for core.runner — Actor-Critic loop engine.

All tests use mocked Actor/Critic to avoid real API calls.
"""

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
    TaskStatus,
    ValidationRule,
)
from core.persistence import TaskPersistence
from core.runner import ActorCriticLoop
from gateway.privacy import PrivacyRedactor
from gateway.provider import LLMResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_node(
    max_attempts: int = 3,
    strategy: LoopStrategy = LoopStrategy.FEEDBACK_DRIVEN,
) -> TaskNode:
    return TaskNode(
        id="test_node",
        name="Test Task",
        actor_prompt_template="Summarize: {{ data }}",
        loop_config=LoopConfig(
            strategy=strategy,
            max_attempts=max_attempts,
            exit_criteria=[
                ValidationRule(type="llm_eval", criteria="Must be good", weight=1.0)
            ],
        ),
    )


def _actor_response(content: str) -> LLMResponse:
    return LLMResponse(
        content=content,
        prompt_tokens=100,
        completion_tokens=50,
        model_name="test-actor",
    )


def _critic_response(passed: bool, score: float = 0.9, critique: str = "") -> EvaluationResult:
    return EvaluationResult(
        passed=passed,
        score=score,
        critique=critique if not passed else None,
        suggestions=[] if passed else ["Try harder"],
        audit_cost=20,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestActorCriticLoop:
    @pytest.mark.asyncio
    async def test_first_attempt_passes(self):
        """Actor output passes Critic on the first try."""
        actor = MagicMock(spec=TaskActor)
        actor.generate = AsyncMock(return_value=_actor_response("Good output"))

        critic = MagicMock(spec=TaskCritic)
        critic.evaluate = AsyncMock(return_value=_critic_response(passed=True, score=0.95))
        critic.provider = MagicMock()
        critic.provider.model_name = "test-critic"

        ledger = TokenLedger(budget_usd=10.0)
        engine = ActorCriticLoop(actor=actor, critic=critic, ledger=ledger)

        node = _make_node(max_attempts=3)
        result = await engine.run(node, "input data")

        assert result["status"] == "success"
        assert result["final_output"] == "Good output"
        assert len(result["history"]) == 1
        assert result["trace"].status == TaskStatus.COMPLETED
        actor.generate.assert_awaited_once()
        critic.evaluate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_retry_then_pass(self):
        """Actor fails once, then passes on second attempt."""
        actor = MagicMock(spec=TaskActor)
        actor.generate = AsyncMock(
            side_effect=[_actor_response("Bad"), _actor_response("Good")]
        )

        critic = MagicMock(spec=TaskCritic)
        critic.evaluate = AsyncMock(
            side_effect=[
                _critic_response(passed=False, score=0.3, critique="Too short"),
                _critic_response(passed=True, score=0.9),
            ]
        )
        critic.provider = MagicMock()
        critic.provider.model_name = "test-critic"

        ledger = TokenLedger(budget_usd=10.0)
        engine = ActorCriticLoop(actor=actor, critic=critic, ledger=ledger)

        node = _make_node(max_attempts=3)
        result = await engine.run(node, "input data")

        assert result["status"] == "success"
        assert result["final_output"] == "Good"
        assert len(result["history"]) == 2
        assert result["history"][0]["passed"] is False
        assert result["history"][0]["critique"] == "Too short"
        assert result["history"][1]["passed"] is True

    @pytest.mark.asyncio
    async def test_exhausted_all_attempts(self):
        """Actor never passes — returns exhausted status."""
        actor = MagicMock(spec=TaskActor)
        actor.generate = AsyncMock(return_value=_actor_response("Always bad"))

        critic = MagicMock(spec=TaskCritic)
        critic.evaluate = AsyncMock(
            return_value=_critic_response(passed=False, score=0.2, critique="Still bad")
        )
        critic.provider = MagicMock()
        critic.provider.model_name = "test-critic"

        ledger = TokenLedger(budget_usd=10.0)
        engine = ActorCriticLoop(actor=actor, critic=critic, ledger=ledger)

        node = _make_node(max_attempts=3)
        result = await engine.run(node, "input data")

        assert result["status"] == "exhausted"
        assert len(result["history"]) == 3
        assert result["trace"].status == TaskStatus.FAILED
        assert actor.generate.await_count == 3
        assert critic.evaluate.await_count == 3

    @pytest.mark.asyncio
    async def test_once_strategy_skips_retries(self):
        """LoopStrategy.ONCE runs exactly one iteration."""
        actor = MagicMock(spec=TaskActor)
        actor.generate = AsyncMock(return_value=_actor_response("Output"))

        critic = MagicMock(spec=TaskCritic)
        critic.evaluate = AsyncMock(
            return_value=_critic_response(passed=False, score=0.5, critique="Fail")
        )
        critic.provider = MagicMock()
        critic.provider.model_name = "test-critic"

        engine = ActorCriticLoop(actor=actor, critic=critic, ledger=None)
        node = _make_node(max_attempts=5, strategy=LoopStrategy.ONCE)
        result = await engine.run(node, "data")

        assert result["status"] == "exhausted"
        assert len(result["history"]) == 1  # only 1 attempt despite max_attempts=5
        actor.generate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_feedback_injected_into_next_attempt(self):
        """Verify that Critic's critique is passed to the Actor on retry."""
        actor = MagicMock(spec=TaskActor)
        actor.generate = AsyncMock(
            side_effect=[_actor_response("Bad"), _actor_response("Fixed")]
        )

        critic = MagicMock(spec=TaskCritic)
        critic.evaluate = AsyncMock(
            side_effect=[
                _critic_response(passed=False, score=0.3, critique="Too vague"),
                _critic_response(passed=True, score=0.9),
            ]
        )
        critic.provider = MagicMock()
        critic.provider.model_name = "test-critic"

        engine = ActorCriticLoop(actor=actor, critic=critic, ledger=None)
        node = _make_node(max_attempts=3)
        await engine.run(node, "data")

        # First call: no feedback
        first_call = actor.generate.call_args_list[0]
        assert first_call.kwargs.get("feedback", first_call[1].get("feedback", "")) == ""

        # Second call: feedback from critic
        second_call = actor.generate.call_args_list[1]
        fb = second_call.kwargs.get("feedback", second_call[1].get("feedback", ""))
        assert fb == "Too vague"

    @pytest.mark.asyncio
    async def test_ledger_records_usage(self):
        """Verify the ledger tracks token consumption across iterations."""
        actor = MagicMock(spec=TaskActor)
        actor.generate = AsyncMock(return_value=_actor_response("Output"))

        critic = MagicMock(spec=TaskCritic)
        critic.evaluate = AsyncMock(return_value=_critic_response(passed=True, score=0.9))
        critic.provider = MagicMock()
        critic.provider.model_name = "test-critic"

        ledger = TokenLedger(budget_usd=10.0)
        engine = ActorCriticLoop(actor=actor, critic=critic, ledger=ledger)

        node = _make_node(max_attempts=1)
        await engine.run(node, "data")

        assert ledger.report.call_count >= 1
        assert ledger.report.actor_prompt_tokens > 0

    @pytest.mark.asyncio
    async def test_no_ledger_no_crash(self):
        """Engine works fine without a ledger."""
        actor = MagicMock(spec=TaskActor)
        actor.generate = AsyncMock(return_value=_actor_response("OK"))

        critic = MagicMock(spec=TaskCritic)
        critic.evaluate = AsyncMock(return_value=_critic_response(passed=True))
        critic.provider = MagicMock()
        critic.provider.model_name = "test-critic"

        engine = ActorCriticLoop(actor=actor, critic=critic, ledger=None)
        node = _make_node(max_attempts=1)
        result = await engine.run(node, "data")

        assert result["status"] == "success"


class TestPrivacyIntegration:
    @pytest.mark.asyncio
    async def test_redactor_masks_input_sent_to_actor(self):
        """Sensitive data should be masked before reaching the Actor."""
        actor = MagicMock(spec=TaskActor)
        # Capture what the Actor receives
        received_inputs = []
        async def capture_generate(**kwargs):
            received_inputs.append(kwargs.get("data", ""))
            return _actor_response("Output: alice@test.com")
        actor.generate = AsyncMock(side_effect=capture_generate)

        critic = MagicMock(spec=TaskCritic)
        critic.evaluate = AsyncMock(return_value=_critic_response(passed=True))
        critic.provider = MagicMock()
        critic.provider.model_name = "test-critic"

        redactor = PrivacyRedactor()
        engine = ActorCriticLoop(actor=actor, critic=critic, redactor=redactor)
        node = _make_node(max_attempts=1)
        result = await engine.run(node, "Contact alice@test.com")

        # Actor should receive masked input
        assert "alice@test.com" not in received_inputs[0]
        assert "[[TR_EMAIL_1]]" in received_inputs[0]
        # Final output should be unmasked
        assert "alice@test.com" in result["final_output"]

    @pytest.mark.asyncio
    async def test_no_redactor_no_crash(self):
        """Engine works without a redactor."""
        actor = MagicMock(spec=TaskActor)
        actor.generate = AsyncMock(return_value=_actor_response("OK"))

        critic = MagicMock(spec=TaskCritic)
        critic.evaluate = AsyncMock(return_value=_critic_response(passed=True))
        critic.provider = MagicMock()
        critic.provider.model_name = "test-critic"

        engine = ActorCriticLoop(actor=actor, critic=critic, redactor=None)
        node = _make_node(max_attempts=1)
        result = await engine.run(node, "data")
        assert result["status"] == "success"


class TestPersistenceIntegration:
    @pytest.mark.asyncio
    async def test_persistence_saves_trace(self):
        """Each iteration should be persisted."""
        actor = MagicMock(spec=TaskActor)
        actor.generate = AsyncMock(return_value=_actor_response("OK"))

        critic = MagicMock(spec=TaskCritic)
        critic.evaluate = AsyncMock(return_value=_critic_response(passed=True))
        critic.provider = MagicMock()
        critic.provider.model_name = "test-critic"

        persistence = MagicMock(spec=TaskPersistence)
        persistence.get_status = MagicMock(return_value=None)

        engine = ActorCriticLoop(
            actor=actor, critic=critic, persistence=persistence
        )
        node = _make_node(max_attempts=1)
        await engine.run(node, "data")

        persistence.save_trace.assert_called_once()
        call_kwargs = persistence.save_trace.call_args.kwargs
        assert call_kwargs["status"] == "completed"

    @pytest.mark.asyncio
    async def test_persistence_skips_completed(self):
        """Already-completed items should be skipped (no API call)."""
        actor = MagicMock(spec=TaskActor)
        actor.generate = AsyncMock(return_value=_actor_response("OK"))

        critic = MagicMock(spec=TaskCritic)
        critic.evaluate = AsyncMock(return_value=_critic_response(passed=True))
        critic.provider = MagicMock()

        persistence = MagicMock(spec=TaskPersistence)
        persistence.get_status = MagicMock(return_value="completed")

        engine = ActorCriticLoop(
            actor=actor, critic=critic, persistence=persistence
        )
        node = _make_node(max_attempts=1)
        result = await engine.run(node, "data")

        assert result["status"] == "success"
        assert result["final_output"] == "(cached)"
        actor.generate.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_persistence_no_crash(self):
        """Engine works without persistence."""
        actor = MagicMock(spec=TaskActor)
        actor.generate = AsyncMock(return_value=_actor_response("OK"))

        critic = MagicMock(spec=TaskCritic)
        critic.evaluate = AsyncMock(return_value=_critic_response(passed=True))
        critic.provider = MagicMock()

        engine = ActorCriticLoop(actor=actor, critic=critic, persistence=None)
        node = _make_node(max_attempts=1)
        result = await engine.run(node, "data")
        assert result["status"] == "success"


class TestFingerprint:
    def test_compute_fingerprint(self):
        fp = ActorCriticLoop.compute_fingerprint(
            model_id="gpt-4o",
            prompt_template="Hello {{ data }}",
            parameters={"temperature": 0.1},
        )
        assert fp.model_id == "gpt-4o"
        assert len(fp.prompt_hash) == 16
        assert fp.parameters["temperature"] == 0.1
        assert fp.parameters["seed"] is None

    def test_verify_fingerprint_match(self):
        fp = ActorCriticLoop.compute_fingerprint(
            "gpt-4o", "Hello {{ data }}", {"temperature": 0.1}
        )
        assert ActorCriticLoop.verify_fingerprint(
            fp, "gpt-4o", "Hello {{ data }}", {"temperature": 0.1}
        )

    def test_verify_fingerprint_mismatch_model(self):
        fp = ActorCriticLoop.compute_fingerprint(
            "gpt-4o", "Hello {{ data }}", {"temperature": 0.1}
        )
        assert not ActorCriticLoop.verify_fingerprint(
            fp, "gpt-4o-mini", "Hello {{ data }}", {"temperature": 0.1}
        )

    def test_verify_fingerprint_mismatch_prompt(self):
        fp = ActorCriticLoop.compute_fingerprint(
            "gpt-4o", "Hello {{ data }}", {"temperature": 0.1}
        )
        assert not ActorCriticLoop.verify_fingerprint(
            fp, "gpt-4o", "Different prompt", {"temperature": 0.1}
        )
