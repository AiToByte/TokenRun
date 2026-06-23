"""Tests for dynamic model routing and self-healing."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from core.models import (
    EvaluationResult,
    LoopConfig,
    ModelTier,
    Runfile,
    TaskNode,
    ValidationRule,
)
from core.orchestrator import TROrchestrator
from core.runner import ActorCriticLoop
from core.ledger import TokenLedger
from core.self_healer import SelfHealer, HealingSuggestion
from gateway.provider import LLMProvider, LLMResponse


# ---------------------------------------------------------------------------
# Dynamic Model Routing
# ---------------------------------------------------------------------------

def _make_node_with_tiers():
    return TaskNode(
        id="n1", name="N1", actor_prompt_template="Do {{ data }}",
        loop_config=LoopConfig(max_attempts=5, exit_criteria=[
            ValidationRule(type="llm_eval", criteria="good")
        ]),
        model_tiers=[
            ModelTier(model="gpt-4o-mini", escalate_after=2),
            ModelTier(model="gpt-4o", escalate_after=3),
        ],
    )


class TestModelTier:
    def test_model_tier_creation(self):
        tier = ModelTier(model="gpt-4o-mini", escalate_after=2)
        assert tier.model == "gpt-4o-mini"
        assert tier.escalate_after == 2
        assert tier.base_url is None

    def test_model_tier_in_task_node(self):
        node = _make_node_with_tiers()
        assert len(node.model_tiers) == 2
        assert node.model_tiers[0].model == "gpt-4o-mini"
        assert node.model_tiers[1].model == "gpt-4o"


class TestDynamicRouting:
    @pytest.mark.asyncio
    async def test_first_attempts_use_tier1(self):
        """First 2 attempts should use tier-1 (gpt-4o-mini)."""
        tier1_provider = MagicMock(spec=LLMProvider)
        tier1_provider.model_name = "gpt-4o-mini"
        tier1_provider.request = AsyncMock(return_value=LLMResponse(
            content="output", prompt_tokens=10, completion_tokens=5, model_name="gpt-4o-mini"
        ))

        tier2_provider = MagicMock(spec=LLMProvider)
        tier2_provider.model_name = "gpt-4o"

        actor = MagicMock()
        actor.provider = tier1_provider
        actor.generate = AsyncMock(return_value=LLMResponse(
            content="output", prompt_tokens=10, completion_tokens=5, model_name="gpt-4o-mini"
        ))

        critic = MagicMock()
        critic.evaluate = AsyncMock(return_value=EvaluationResult(
            passed=False, score=0.3, critique="bad", audit_cost=5
        ))
        critic.provider = MagicMock()
        critic.provider.model_name = "m"

        engine = ActorCriticLoop(
            actor=actor, critic=critic,
            model_providers={"gpt-4o-mini": tier1_provider, "gpt-4o": tier2_provider},
        )

        node = _make_node_with_tiers()
        result = await engine.run(node, "data")

        # All 5 attempts should have been made
        assert len(result["history"]) == 5

    def test_resolve_tier_provider_tier1(self):
        """Attempts 1-2 should resolve to tier-1."""
        tier1 = MagicMock(spec=LLMProvider)
        tier2 = MagicMock(spec=LLMProvider)

        engine = ActorCriticLoop(
            actor=MagicMock(), critic=MagicMock(),
            model_providers={"gpt-4o-mini": tier1, "gpt-4o": tier2},
        )

        node = _make_node_with_tiers()
        assert engine._resolve_tier_provider(node, 1) is tier1
        assert engine._resolve_tier_provider(node, 2) is tier1

    def test_resolve_tier_provider_tier2(self):
        """Attempts 3+ should resolve to tier-2."""
        tier1 = MagicMock(spec=LLMProvider)
        tier2 = MagicMock(spec=LLMProvider)

        engine = ActorCriticLoop(
            actor=MagicMock(), critic=MagicMock(),
            model_providers={"gpt-4o-mini": tier1, "gpt-4o": tier2},
        )

        node = _make_node_with_tiers()
        assert engine._resolve_tier_provider(node, 3) is tier2
        assert engine._resolve_tier_provider(node, 4) is tier2
        assert engine._resolve_tier_provider(node, 5) is tier2

    def test_resolve_tier_no_tiers_returns_none(self):
        """No tiers configured should return None."""
        engine = ActorCriticLoop(actor=MagicMock(), critic=MagicMock())
        node = TaskNode(
            id="n1", name="N1", actor_prompt_template="Do {{ data }}",
            loop_config=LoopConfig(max_attempts=3, exit_criteria=[
                ValidationRule(type="llm_eval", criteria="good")
            ]),
        )
        assert engine._resolve_tier_provider(node, 1) is None

    def test_resolve_tier_no_providers_returns_none(self):
        """No providers configured should return None."""
        engine = ActorCriticLoop(actor=MagicMock(), critic=MagicMock())
        node = _make_node_with_tiers()
        assert engine._resolve_tier_provider(node, 3) is None


# ---------------------------------------------------------------------------
# Self-Healing
# ---------------------------------------------------------------------------

class TestSelfHealer:
    def test_record_critique(self):
        healer = SelfHealer(min_pattern_frequency=2)
        healer.record_critique("语气太生硬")
        healer.record_critique("语气不够自然")
        assert len(healer._critiques) == 2

    def test_no_healing_below_threshold(self):
        healer = SelfHealer(min_pattern_frequency=3)
        healer.record_critique("语气太生硬")
        healer.record_critique("语气不够自然")
        assert healer.check_healing_needed() is None

    def test_healing_triggered_at_threshold(self):
        healer = SelfHealer(min_pattern_frequency=3)
        healer.record_critique("语气太生硬，需要更自然")
        healer.record_critique("语气太生硬，不够流畅")
        healer.record_critique("语气太生硬，缺乏亲和力")
        suggestion = healer.check_healing_needed()
        assert suggestion is not None
        assert suggestion.frequency >= 3
        assert "语气太生硬" in suggestion.critique_pattern

    def test_healing_confidence(self):
        healer = SelfHealer(min_pattern_frequency=2)
        for _ in range(10):
            healer.record_critique("格式不对")
        suggestion = healer.check_healing_needed()
        assert suggestion is not None
        assert suggestion.confidence == 1.0  # 10/10 = 1.0

    def test_reset_clears_critiques(self):
        healer = SelfHealer()
        healer.record_critique("test")
        healer.reset()
        assert len(healer._critiques) == 0

    @pytest.mark.asyncio
    async def test_generate_healing_with_meta_model(self):
        meta_provider = MagicMock(spec=LLMProvider)
        meta_provider.request = AsyncMock(return_value=LLMResponse(
            content="改进后的 Prompt：请用更自然的语气。{{ data }}",
            prompt_tokens=50, completion_tokens=20, model_name="gpt-4o",
        ))

        healer = SelfHealer(meta_provider=meta_provider, min_pattern_frequency=2)
        for _ in range(3):
            healer.record_critique("语气太生硬")

        node = TaskNode(
            id="n1", name="N1", actor_prompt_template="原始 Prompt：{{ data }}",
            loop_config=LoopConfig(max_attempts=3, exit_criteria=[
                ValidationRule(type="llm_eval", criteria="good")
            ]),
        )

        suggestion = await healer.generate_healing(node)
        assert suggestion is not None
        assert suggestion.suggested_prompt != node.actor_prompt_template
        assert "改进后" in suggestion.suggested_prompt

    def test_apply_healing_creates_version(self):
        healer = SelfHealer()
        node = TaskNode(
            id="n1", name="N1", actor_prompt_template="Original {{ data }}",
            loop_config=LoopConfig(max_attempts=3, exit_criteria=[
                ValidationRule(type="llm_eval", criteria="good")
            ]),
        )
        # Register initial version first
        from core.prompt_lineage import PromptLineageManager
        mgr = PromptLineageManager()
        mgr.register_initial(node, "Original {{ data }}")

        suggestion = HealingSuggestion(
            original_prompt="Original {{ data }}",
            suggested_prompt="Improved {{ data }}",
            critique_pattern="语气太生硬",
            frequency=5,
            confidence=0.5,
        )

        version = healer.apply_healing(node, suggestion, "auto-heal test")
        assert version.version_id == "v1.1"
        assert node.actor_prompt_template == "Improved {{ data }}"
        assert len(healer._critiques) == 0  # cleared after healing

    @pytest.mark.asyncio
    async def test_generate_healing_no_meta_model(self):
        healer = SelfHealer(meta_provider=None, min_pattern_frequency=1)
        for _ in range(5):
            healer.record_critique("test")

        node = TaskNode(
            id="n1", name="N1", actor_prompt_template="P: {{ data }}",
            loop_config=LoopConfig(max_attempts=3, exit_criteria=[
                ValidationRule(type="llm_eval", criteria="good")
            ]),
        )
        suggestion = await healer.generate_healing(node)
        assert suggestion is None


class TestOrchestratorSelfHealer:
    @pytest.mark.asyncio
    async def test_critiques_recorded_to_healer(self):
        """Orchestrator should pass critiques to SelfHealer."""
        node = TaskNode(
            id="n1", name="N1", actor_prompt_template="Do {{ data }}",
            loop_config=LoopConfig(max_attempts=1, exit_criteria=[
                ValidationRule(type="llm_eval", criteria="good")
            ]),
        )
        runfile = Runfile(workflow=[node])

        engine = MagicMock(spec=ActorCriticLoop)
        engine.run = AsyncMock(return_value={
            "status": "exhausted", "final_output": "bad",
            "history": [
                {"iteration": 1, "score": 0.3, "critique": "语气太生硬"},
                {"iteration": 2, "score": 0.4, "critique": "语气太生硬"},
            ],
            "trace": MagicMock(),
        })

        ledger = TokenLedger(budget_usd=10.0)
        healer = SelfHealer(min_pattern_frequency=2)
        orch = TROrchestrator(
            runfile=runfile, loop_engine=engine, ledger=ledger,
            self_healer=healer,
        )

        await orch.run_mass_production(["data"])

        assert len(healer._critiques) == 2
        assert healer._critiques[0] == "语气太生硬"
