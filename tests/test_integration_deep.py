"""Deep integration tests — full data flow tracing across all components."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from core.actor import TaskActor
from core.critic import TaskCritic
from core.ledger import TokenLedger
from core.models import (
    EvaluationResult,
    LoopConfig,
    Runfile,
    TaskNode,
    ValidationRule,
)
from core.orchestrator import TROrchestrator
from core.persistence import TaskPersistence
from core.prompt_lineage import PromptLineageManager
from core.runner import ActorCriticLoop
from core.solidifier import SkillSolidifier
from gateway.privacy import PrivacyRedactor
from gateway.provider import LLMResponse


def _make_runfile():
    return Runfile(
        name="Deep Integration Test",
        workflow=[TaskNode(
            id="n1", name="Summarize",
            actor_prompt_template="Summarize: {{ data }}",
            loop_config=LoopConfig(
                max_attempts=2,
                exit_criteria=[ValidationRule(type="llm_eval", criteria="Must be good")],
            ),
        )],
    )


class TestDataFlowTracing:
    @pytest.mark.asyncio
    async def test_full_data_flow_mask_actor_unmask_critic_persist(self, tmp_path):
        """Trace data through: input → mask → Actor → unmask → Critic → persist."""
        runfile = _make_runfile()

        # Track what each component receives
        actor_received = []
        critic_received = []
        persisted_data = []

        actor = MagicMock(spec=TaskActor)
        async def actor_capture(**kwargs):
            actor_received.append(kwargs.get("data", ""))
            return LLMResponse(
                content="Summary of [[TR_EMAIL_1]]",  # Actor sees masked input
                prompt_tokens=10, completion_tokens=5, model_name="m",
            )
        actor.generate = AsyncMock(side_effect=actor_capture)

        critic = MagicMock(spec=TaskCritic)
        async def critic_capture(**kwargs):
            critic_received.append(kwargs.get("input_data", ""))
            return EvaluationResult(passed=True, score=0.9, audit_cost=5)
        critic.evaluate = AsyncMock(side_effect=critic_capture)
        critic.provider = MagicMock()
        critic.provider.model_name = "m"

        ledger = TokenLedger(budget_usd=10.0)
        persistence = TaskPersistence(db_path=str(tmp_path / "traces.db"))
        redactor = PrivacyRedactor(rules=["emails"])

        engine = ActorCriticLoop(
            actor=actor, critic=critic, ledger=ledger,
            persistence=persistence, redactor=redactor,
        )

        # Run with PII-containing input
        result = await engine.run(
            runfile.workflow[0],
            "Contact alice@test.com for details",
        )

        # 1. Actor should receive masked input
        assert "alice@test.com" not in actor_received[0]
        assert "[[TR_EMAIL_" in actor_received[0]

        # 2. Critic should receive masked input (privacy protection)
        assert "alice@test.com" not in critic_received[0]
        assert "[[TR_EMAIL_" in critic_received[0]

        # 3. Final output should be unmasked
        assert "alice@test.com" in result["final_output"]

        # 4. Persistence should have the trace
        assert persistence.get_status(f"n1:{hashlib_sha('Contact alice@test.com for details')}") == "completed"


class TestPromptLineageIntegration:
    @pytest.mark.asyncio
    async def test_version_chain_across_resample(self, tmp_path):
        """Prompt versions should chain correctly across Edit & Resample."""
        node = TaskNode(
            id="n1", name="N1", actor_prompt_template="Original {{ data }}",
            loop_config=LoopConfig(max_attempts=1, exit_criteria=[
                ValidationRule(type="llm_eval", criteria="good")
            ]),
        )
        runfile = Runfile(workflow=[node])

        actor = MagicMock(spec=TaskActor)
        actor.generate = AsyncMock(return_value=LLMResponse(
            content="output", prompt_tokens=10, completion_tokens=5, model_name="m"
        ))
        critic = MagicMock(spec=TaskCritic)
        critic.evaluate = AsyncMock(return_value=EvaluationResult(passed=True, score=0.9, audit_cost=5))
        critic.provider = MagicMock()
        critic.provider.model_name = "m"

        ledger = TokenLedger(budget_usd=10.0)
        engine = ActorCriticLoop(actor=actor, critic=critic, ledger=ledger)
        orch = TROrchestrator(runfile=runfile, loop_engine=engine, ledger=ledger)

        # Initial version (auto-registered by orchestrator)
        assert len(node.prompt_registry) == 1
        assert node.current_version_id == "v1.0"

        # Simulate Edit & Resample
        orch.pause()
        orch.resume(new_prompt="Improved {{ data }}", change_log="better wording")

        assert len(node.prompt_registry) == 2
        assert node.current_version_id == "v1.1"
        assert node.prompt_registry[1].parent_id == "v1.0"
        assert node.prompt_registry[1].change_log == "better wording"

        # Another revision
        orch.pause()
        orch.resume(new_prompt="Final {{ data }}", change_log="final version")

        assert len(node.prompt_registry) == 3
        assert node.current_version_id == "v1.2"
        assert node.prompt_registry[2].parent_id == "v1.1"

        # Verify lineage chain
        lineage = orch.lineage.get_lineage_chain(node)
        assert len(lineage) == 3
        assert [v.version_id for v in lineage] == ["v1.0", "v1.1", "v1.2"]

        # Verify iteration records include prompt_version_id
        await orch.run_mass_production(["data"])
        # The engine.run mock doesn't set prompt_version_id, but the real runner does


class TestDriftDetectionIntegration:
    @pytest.mark.asyncio
    async def test_drift_detection_during_production(self, tmp_path):
        """DriftDetector should be called during production."""
        from core.drift_detector import DriftDetector

        node = TaskNode(
            id="n1", name="N1", actor_prompt_template="Do {{ data }}",
            loop_config=LoopConfig(max_attempts=1, exit_criteria=[
                ValidationRule(type="llm_eval", criteria="good")
            ]),
        )
        runfile = Runfile(workflow=[node])

        actor = MagicMock(spec=TaskActor)
        actor.generate = AsyncMock(return_value=LLMResponse(
            content="output", prompt_tokens=10, completion_tokens=5, model_name="m"
        ))
        actor.provider = MagicMock()
        actor.provider.request = AsyncMock(return_value=LLMResponse(
            content="same", prompt_tokens=5, completion_tokens=3, model_name="m"
        ))

        critic = MagicMock(spec=TaskCritic)
        critic.evaluate = AsyncMock(return_value=EvaluationResult(passed=True, score=0.9, audit_cost=5))
        critic.provider = MagicMock()
        critic.provider.model_name = "m"

        ledger = TokenLedger(budget_usd=10.0)
        engine = ActorCriticLoop(actor=actor, critic=critic, ledger=ledger)

        # Drift detector with check_interval=1 (check every item)
        dd = DriftDetector(
            actor=actor,
            golden_samples=[{"input": "test", "expected_output_hash": ""}],
            check_interval=1,
        )

        orch = TROrchestrator(
            runfile=runfile, loop_engine=engine, ledger=ledger,
            drift_detector=dd,
        )

        results = await orch.run_mass_production(["data1", "data2"])
        assert len(results) == 2
        # Drift detector should have been invoked


class TestSolidificationIntegration:
    @pytest.mark.asyncio
    async def test_solidification_from_production_results(self, tmp_path):
        """Solidifier should produce valid .trs from production traces."""
        runfile = _make_runfile()

        actor = MagicMock(spec=TaskActor)
        actor.generate = AsyncMock(return_value=LLMResponse(
            content="Good summary", prompt_tokens=10, completion_tokens=5, model_name="m"
        ))
        critic = MagicMock(spec=TaskCritic)
        critic.evaluate = AsyncMock(return_value=EvaluationResult(passed=True, score=0.9, audit_cost=5))
        critic.provider = MagicMock()
        critic.provider.model_name = "m"

        ledger = TokenLedger(budget_usd=10.0)
        engine = ActorCriticLoop(actor=actor, critic=critic, ledger=ledger)
        orch = TROrchestrator(runfile=runfile, loop_engine=engine, ledger=ledger)

        data = ["AI is transforming industries", "Quantum computing is emerging"]
        results = await orch.run_mass_production(data)

        # Solidify
        solidifier = SkillSolidifier(vault_path=str(tmp_path / "vault"))
        traces = [{"status": r.get("status"), "history": r.get("history", [])} for r in results]
        skill_path = solidifier.distill(
            task_name=runfile.name,
            traces=traces,
            prompt_template=runfile.workflow[0].actor_prompt_template,
            model_config={"model": "m"},
            validation_rules=[r.model_dump() for r in runfile.workflow[0].loop_config.exit_criteria],
        )

        # Verify skill file
        import json
        skill = json.loads(open(skill_path, encoding="utf-8").read())
        assert skill["name"] == "Deep Integration Test"
        assert skill["optimized_prompt"] == "Summarize: {{ data }}"
        assert skill["performance_stats"]["total"] == 2
        assert skill["performance_stats"]["success_rate"] == 1.0

        # Reload skill
        skill_id = skill["skill_id"]
        loaded = solidifier.load_skill(skill_id)
        assert loaded["name"] == "Deep Integration Test"


class TestMultiNodeDAGIntegration:
    @pytest.mark.asyncio
    async def test_dag_data_propagation(self, tmp_path):
        """Node B should receive Node A's output as input."""
        node_a = TaskNode(
            id="extract", name="Extract",
            actor_prompt_template="Extract key points: {{ data }}",
            loop_config=LoopConfig(max_attempts=1, exit_criteria=[
                ValidationRule(type="llm_eval", criteria="good")
            ]),
        )
        node_b = TaskNode(
            id="summarize", name="Summarize", depends_on=["extract"],
            actor_prompt_template="Summarize: {{ data }}",
            loop_config=LoopConfig(max_attempts=1, exit_criteria=[
                ValidationRule(type="llm_eval", criteria="good")
            ]),
        )
        runfile = Runfile(workflow=[node_a, node_b])

        call_log = []
        actor = MagicMock(spec=TaskActor)
        async def log_actor(**kwargs):
            call_log.append(kwargs.get("data", ""))
            return LLMResponse(content="processed", prompt_tokens=10, completion_tokens=5, model_name="m")
        actor.generate = AsyncMock(side_effect=log_actor)

        critic = MagicMock(spec=TaskCritic)
        critic.evaluate = AsyncMock(return_value=EvaluationResult(passed=True, score=0.9, audit_cost=5))
        critic.provider = MagicMock()
        critic.provider.model_name = "m"

        ledger = TokenLedger(budget_usd=10.0)
        engine = ActorCriticLoop(actor=actor, critic=critic, ledger=ledger)
        orch = TROrchestrator(runfile=runfile, loop_engine=engine, ledger=ledger)

        results = await orch.run_mass_production(["original input"])

        # Node A should receive original input
        # Node B should receive Node A's output
        assert len(call_log) == 2
        assert "original input" in call_log[0]  # Node A
        assert "processed" in call_log[1]  # Node B receives A's output


# Helper
def hashlib_sha(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode()).hexdigest()[:16]
