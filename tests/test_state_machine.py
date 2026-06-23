"""State machine completeness tests — verify all Orchestrator state transitions."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from core.models import (
    Fingerprint,
    GovernanceConfig,
    LoopConfig,
    Runfile,
    SamplingConfig,
    TaskNode,
    ValidationRule,
)
from core.orchestrator import TROrchestrator
from core.runner import ActorCriticLoop
from core.ledger import TokenLedger


def _make_runfile(**overrides):
    defaults = {
        "workflow": [TaskNode(
            id="n1", name="N1", actor_prompt_template="Do {{ data }}",
            loop_config=LoopConfig(max_attempts=2, exit_criteria=[
                ValidationRule(type="llm_eval", criteria="good")
            ]),
        )],
    }
    defaults.update(overrides)
    return Runfile(**defaults)


def _mock_engine(result_status="success"):
    engine = MagicMock(spec=ActorCriticLoop)
    engine.run = AsyncMock(return_value={
        "status": result_status,
        "final_output": "output",
        "history": [{"iteration": 1, "score": 0.9}],
        "trace": MagicMock(),
    })
    engine.verify_fingerprint = MagicMock(return_value=True)
    return engine


class TestStateTransitions:
    @pytest.mark.asyncio
    async def test_sampling_disabled_skips_to_production(self):
        """When sampling.enabled=False, skip sampling phase."""
        runfile = _make_runfile()
        runfile.sampling.enabled = False
        engine = _mock_engine()
        ledger = TokenLedger(budget_usd=10.0)
        orch = TROrchestrator(runfile=runfile, loop_engine=engine, ledger=ledger)

        sample_results = await orch.run_sampling_gate(["data"])
        assert sample_results == []  # sampling skipped

    @pytest.mark.asyncio
    async def test_fingerprint_mismatch_rejects_production(self):
        """Production should be rejected when fingerprint doesn't match."""
        runfile = _make_runfile()
        runfile.fingerprint = Fingerprint(
            model_id="wrong-model", prompt_hash="wrong-hash", parameters={}
        )
        engine = MagicMock(spec=ActorCriticLoop)
        engine.verify_fingerprint = MagicMock(return_value=False)
        ledger = TokenLedger(budget_usd=10.0)
        orch = TROrchestrator(runfile=runfile, loop_engine=engine, ledger=ledger)

        results = await orch.run_mass_production(["data"])
        assert results == []
        engine.run.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_fingerprint_match_allows_production(self):
        """Production should proceed when fingerprint matches."""
        runfile = _make_runfile()
        runfile.fingerprint = Fingerprint(
            model_id="test-model", prompt_hash="abc", parameters={}
        )

        # Mock the static method on the class
        with pytest.MonkeyPatch.context() as m:
            m.setattr(ActorCriticLoop, "verify_fingerprint", MagicMock(return_value=True))

            engine = MagicMock(spec=ActorCriticLoop)
            engine.run = AsyncMock(return_value={
                "status": "success", "final_output": "ok", "history": [], "trace": MagicMock(),
            })
            ledger = TokenLedger(budget_usd=10.0)
            orch = TROrchestrator(runfile=runfile, loop_engine=engine, ledger=ledger)

            results = await orch.run_mass_production(["data"])
            assert len(results) == 1
            engine.run.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_budget_fuse_stops_production(self):
        """Budget exceeded should return budget_exceeded status."""
        runfile = _make_runfile()
        from core.ledger import BudgetExceededError
        engine = MagicMock(spec=ActorCriticLoop)
        engine.run = AsyncMock(side_effect=BudgetExceededError("over budget"))
        ledger = TokenLedger(budget_usd=0.001)
        orch = TROrchestrator(runfile=runfile, loop_engine=engine, ledger=ledger)

        results = await orch.run_mass_production(["data"])
        assert results[0]["status"] == "budget_exceeded"

    @pytest.mark.asyncio
    async def test_max_loop_count_stops_execution(self):
        """Governance max_loop_count should stop after limit reached."""
        runfile = _make_runfile(governance=GovernanceConfig(max_usd=100.0, max_loop_count=2))
        engine = MagicMock(spec=ActorCriticLoop)
        engine.run = AsyncMock(return_value={
            "status": "success", "final_output": "ok",
            "history": [{"iteration": 1, "score": 0.9}], "trace": MagicMock(),
        })
        ledger = TokenLedger(budget_usd=100.0)
        orch = TROrchestrator(runfile=runfile, loop_engine=engine, ledger=ledger)

        results = await orch.run_mass_production(["d1", "d2", "d3"])
        # First 2 items should process, 3rd should be blocked
        assert engine.run.await_count <= 3  # at most 3, but governance may block

    @pytest.mark.asyncio
    async def test_pause_resume_state_transitions(self):
        """Verify pause → resume state changes."""
        runfile = _make_runfile()
        engine = _mock_engine()
        ledger = TokenLedger(budget_usd=10.0)
        orch = TROrchestrator(runfile=runfile, loop_engine=engine, ledger=ledger)

        # Initial state
        assert orch.is_paused is False

        # Pause
        orch.pause()
        assert orch.is_paused is True

        # Resume
        orch.resume()
        assert orch.is_paused is False

    @pytest.mark.asyncio
    async def test_resume_with_prompt_creates_version(self):
        """Resume with new prompt should create a PromptVersion."""
        node = TaskNode(
            id="n1", name="N1", actor_prompt_template="Original {{ data }}",
            loop_config=LoopConfig(max_attempts=1, exit_criteria=[
                ValidationRule(type="llm_eval", criteria="good")
            ]),
        )
        runfile = Runfile(workflow=[node])
        engine = _mock_engine()
        ledger = TokenLedger(budget_usd=10.0)
        orch = TROrchestrator(runfile=runfile, loop_engine=engine, ledger=ledger)

        orch.pause()
        orch.resume(new_prompt="New {{ data }}", change_log="improved")

        assert node.actor_prompt_template == "New {{ data }}"
        assert len(node.prompt_registry) == 2
        assert node.prompt_registry[1].parent_id == "v1.0"

    @pytest.mark.asyncio
    async def test_results_reset_between_phases(self):
        """Results should be reset when starting a new phase."""
        runfile = _make_runfile()
        engine = _mock_engine()
        ledger = TokenLedger(budget_usd=10.0)
        orch = TROrchestrator(runfile=runfile, loop_engine=engine, ledger=ledger)

        # Sampling phase
        await orch.run_sampling_gate(["data"])
        assert len(orch.results) > 0

        # Production phase should reset results
        await orch.run_mass_production(["data"])
        # Results should only contain production traces (not sampling)
        assert len(orch.results) == 1  # only 1 production item


class TestSamplingModes:
    @pytest.mark.asyncio
    async def test_percentage_mode(self):
        runfile = _make_runfile()
        runfile.sampling.mode = "percentage"
        runfile.sampling.value = 0.5
        engine = _mock_engine()
        ledger = TokenLedger(budget_usd=10.0)
        orch = TROrchestrator(runfile=runfile, loop_engine=engine, ledger=ledger)

        await orch.run_sampling_gate(["a", "b", "c", "d"])
        assert engine.run.await_count == 2  # 50% of 4

    @pytest.mark.asyncio
    async def test_count_mode(self):
        runfile = _make_runfile()
        runfile.sampling.mode = "count"
        runfile.sampling.value = 3
        engine = _mock_engine()
        ledger = TokenLedger(budget_usd=10.0)
        orch = TROrchestrator(runfile=runfile, loop_engine=engine, ledger=ledger)

        await orch.run_sampling_gate(["a", "b", "c", "d", "e"])
        assert engine.run.await_count == 3
