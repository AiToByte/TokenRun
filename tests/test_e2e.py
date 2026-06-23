"""End-to-end integration tests — full mission lifecycle with mocked LLM."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from core.actor import TaskActor
from core.critic import TaskCritic
from core.ledger import TokenLedger
from core.models import (
    EvaluationResult,
    LoopConfig,
    Runfile,
    SamplingConfig,
    SecurityConfig,
    TaskNode,
    ValidationRule,
)
from core.orchestrator import TROrchestrator
from core.persistence import TaskPersistence
from core.prompt_lineage import PromptLineageManager
from core.runner import ActorCriticLoop
from core.sampling_manager import SamplingManager
from core.solidifier import SkillSolidifier
from gateway.privacy import PrivacyRedactor
from gateway.provider import LLMResponse


def _make_runfile():
    return Runfile(
        name="E2E Test Mission",
        security=SecurityConfig(masking_rules=["emails", "api_keys"]),
        sampling=SamplingConfig(enabled=True, mode="percentage", value=0.5, auto_pause=False),
        workflow=[TaskNode(
            id="summarizer",
            name="Summarize",
            actor_prompt_template="Summarize: {{ data }}",
            loop_config=LoopConfig(
                max_attempts=2,
                exit_criteria=[ValidationRule(type="llm_eval", criteria="Must be good")],
            ),
        )],
    )


def _mock_actor():
    actor = MagicMock(spec=TaskActor)
    actor.generate = AsyncMock(return_value=LLMResponse(
        content="Summary of the input",
        prompt_tokens=50,
        completion_tokens=20,
        model_name="test-actor",
    ))
    return actor


def _mock_critic(passed=True):
    critic = MagicMock(spec=TaskCritic)
    critic.evaluate = AsyncMock(return_value=EvaluationResult(
        passed=passed, score=0.9, audit_cost=10,
    ))
    critic.provider = MagicMock()
    critic.provider.model_name = "test-critic"
    return critic


class TestE2EMissionLifecycle:
    @pytest.mark.asyncio
    async def test_full_mission_sampling_to_production(self, tmp_path):
        """Full lifecycle: sampling → production → solidification."""
        runfile = _make_runfile()
        actor = _mock_actor()
        critic = _mock_critic(passed=True)

        ledger = TokenLedger(budget_usd=10.0)
        persistence = TaskPersistence(db_path=str(tmp_path / "traces.db"))
        redactor = PrivacyRedactor(rules=runfile.security.masking_rules)
        engine = ActorCriticLoop(actor=actor, critic=critic, ledger=ledger, persistence=persistence, redactor=redactor)
        orch = TROrchestrator(runfile=runfile, loop_engine=engine, ledger=ledger)

        data = ["Item 1 about AI", "Item 2 about quantum", "Item 3 about climate", "Item 4 about tech"]

        # Phase 1: Sampling
        sample_results = await orch.run_sampling_gate(data)
        assert len(sample_results) >= 1
        assert all(r.get("status") == "success" for r in sample_results)

        # Phase 2: Production
        full_results = await orch.run_mass_production(data)
        assert len(full_results) == 4
        success_count = sum(1 for r in full_results if r.get("status") == "success")
        assert success_count == 4

        # Phase 3: Solidification
        solidifier = SkillSolidifier(vault_path=str(tmp_path / "vault"))
        traces = [{"status": r.get("status"), "history": r.get("history", [])} for r in full_results]
        skill_path = solidifier.distill(
            task_name=runfile.name,
            traces=traces,
            prompt_template=runfile.workflow[0].actor_prompt_template,
        )
        assert skill_path.endswith(".trs")

        # Verify cleanup
        redactor.clear_vault()
        assert redactor.vault_size == 0

        # Verify ledger
        summary = ledger.get_summary()
        assert float(summary["total_cost"].replace("$", "")) > 0

    @pytest.mark.asyncio
    async def test_mission_with_pii_redaction(self, tmp_path):
        """Verify PII is redacted throughout the pipeline."""
        runfile = _make_runfile()

        captured_inputs = []
        actor = MagicMock(spec=TaskActor)
        async def capture_actor(**kwargs):
            captured_inputs.append(kwargs.get("data", ""))
            return LLMResponse(content="Done", prompt_tokens=10, completion_tokens=5, model_name="m")
        actor.generate = AsyncMock(side_effect=capture_actor)

        critic = _mock_critic()
        ledger = TokenLedger(budget_usd=10.0)
        redactor = PrivacyRedactor(rules=["emails"])
        engine = ActorCriticLoop(actor=actor, critic=critic, ledger=ledger, redactor=redactor)
        orch = TROrchestrator(runfile=runfile, loop_engine=engine, ledger=ledger)

        await orch.run_mass_production(["Contact alice@test.com"])

        # Verify actor received masked input
        assert "alice@test.com" not in captured_inputs[0]
        assert "[[TR_EMAIL_" in captured_inputs[0]

    @pytest.mark.asyncio
    async def test_mission_with_dag(self, tmp_path):
        """Multi-node DAG execution."""
        node_a = TaskNode(
            id="extract", name="Extract",
            actor_prompt_template="Extract: {{ data }}",
            loop_config=LoopConfig(max_attempts=1, exit_criteria=[
                ValidationRule(type="llm_eval", criteria="good")
            ]),
        )
        node_b = TaskNode(
            id="summarize", name="Summarize",
            depends_on=["extract"],
            actor_prompt_template="Summarize: {{ data }}",
            loop_config=LoopConfig(max_attempts=1, exit_criteria=[
                ValidationRule(type="llm_eval", criteria="good")
            ]),
        )
        runfile = Runfile(workflow=[node_a, node_b])

        actor = _mock_actor()
        critic = _mock_critic()
        ledger = TokenLedger(budget_usd=10.0)
        engine = ActorCriticLoop(actor=actor, critic=critic, ledger=ledger)
        orch = TROrchestrator(runfile=runfile, loop_engine=engine, ledger=ledger)

        results = await orch.run_mass_production(["input data"])
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_mission_with_fingerprint_verification(self, tmp_path):
        """Fingerprint should be verified before production."""
        runfile = _make_runfile()
        actor = _mock_actor()
        critic = _mock_critic()
        ledger = TokenLedger(budget_usd=10.0)
        engine = ActorCriticLoop(actor=actor, critic=critic, ledger=ledger)
        orch = TROrchestrator(runfile=runfile, loop_engine=engine, ledger=ledger)

        # Set a fingerprint with wrong model
        from core.models import Fingerprint
        runfile.fingerprint = Fingerprint(model_id="wrong-model", prompt_hash="abc")

        results = await orch.run_mass_production(["data"])
        # Should be rejected due to fingerprint mismatch
        assert results == []

    @pytest.mark.asyncio
    async def test_mission_persistence_checkpoint(self, tmp_path):
        """Completed items should be skipped on re-run."""
        runfile = _make_runfile()
        actor = _mock_actor()
        critic = _mock_critic()
        ledger = TokenLedger(budget_usd=10.0)
        persistence = TaskPersistence(db_path=str(tmp_path / "traces.db"))
        engine = ActorCriticLoop(actor=actor, critic=critic, ledger=ledger, persistence=persistence)
        orch = TROrchestrator(runfile=runfile, loop_engine=engine, ledger=ledger)

        # First run
        await orch.run_mass_production(["item1"])
        assert actor.generate.await_count == 1

        # Second run — should use cache
        actor.generate.reset_mock()
        engine2 = ActorCriticLoop(actor=actor, critic=critic, ledger=ledger, persistence=persistence)
        orch2 = TROrchestrator(runfile=runfile, loop_engine=engine2, ledger=ledger)
        results = await orch2.run_mass_production(["item1"])

        # Actor should NOT be called (cached)
        actor.generate.assert_not_awaited()
        assert results[0]["status"] == "success"
        assert results[0]["final_output"] == "(cached)"
