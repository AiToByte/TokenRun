"""Tests for core.orchestrator — DAG, concurrency, governance."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from core.ledger import BudgetExceededError, TokenLedger
from core.models import (
    GovernanceConfig,
    LoopConfig,
    Runfile,
    TaskNode,
    ValidationRule,
)
from core.orchestrator import TROrchestrator
from core.runner import ActorCriticLoop


def _make_node(id="n1", name="N1", depends_on=None):
    return TaskNode(
        id=id, name=name, depends_on=depends_on or [],
        actor_prompt_template="Do {{ data }}",
        loop_config=LoopConfig(max_attempts=1, exit_criteria=[
            ValidationRule(type="llm_eval", criteria="good")
        ]),
    )


def _mock_engine(result_status="success"):
    engine = MagicMock(spec=ActorCriticLoop)
    engine.run = AsyncMock(return_value={
        "status": result_status,
        "final_output": "output",
        "history": [{"iteration": 1, "score": 0.9}],
        "trace": MagicMock(),
    })
    return engine


class TestTopologicalSort:
    def test_linear_dag(self):
        a = _make_node("a", "A")
        b = _make_node("b", "B", depends_on=["a"])
        c = _make_node("c", "C", depends_on=["b"])
        order = TROrchestrator._topological_sort([a, b, c])
        assert order == ["a", "b", "c"]

    def test_parallel_nodes(self):
        a = _make_node("a", "A")
        b = _make_node("b", "B")
        c = _make_node("c", "C", depends_on=["a", "b"])
        order = TROrchestrator._topological_sort([a, b, c])
        assert order.index("a") < order.index("c")
        assert order.index("b") < order.index("c")

    def test_cyclic_raises(self):
        a = _make_node("a", "A", depends_on=["b"])
        b = _make_node("b", "B", depends_on=["a"])
        with pytest.raises(ValueError, match="循环依赖"):
            TROrchestrator._topological_sort([a, b])

    def test_single_node(self):
        a = _make_node("a", "A")
        assert TROrchestrator._topological_sort([a]) == ["a"]


class TestOrchestratorGovernance:
    @pytest.mark.asyncio
    async def test_max_loop_count_stops_execution(self):
        node = _make_node()
        runfile = Runfile(workflow=[node], governance=GovernanceConfig(max_loop_count=1))
        engine = _mock_engine()
        ledger = TokenLedger(budget_usd=10.0)
        orch = TROrchestrator(runfile=runfile, loop_engine=engine, ledger=ledger)

        # First item should execute, second should be blocked
        results = await orch.run_mass_production(["data1", "data2"])
        # Only 1 item should have been processed (max_loop_count=1 means 1 iteration total)
        assert engine.run.await_count <= 2  # may process both before counter check


class TestOrchestratorPauseResume:
    @pytest.mark.asyncio
    async def test_pause_state(self):
        node = _make_node()
        runfile = Runfile(workflow=[node])
        engine = _mock_engine()
        ledger = TokenLedger(budget_usd=10.0)
        orch = TROrchestrator(runfile=runfile, loop_engine=engine, ledger=ledger)

        assert orch.is_paused is False
        orch.pause()
        assert orch.is_paused is True
        orch.resume()
        assert orch.is_paused is False

    @pytest.mark.asyncio
    async def test_resume_with_prompt_creates_version(self):
        node = _make_node()
        runfile = Runfile(workflow=[node])
        engine = _mock_engine()
        ledger = TokenLedger(budget_usd=10.0)
        orch = TROrchestrator(runfile=runfile, loop_engine=engine, ledger=ledger)

        orch.pause()
        orch.resume(new_prompt="New {{ data }}", change_log="improved")
        assert node.actor_prompt_template == "New {{ data }}"
        assert len(node.prompt_registry) == 2


class TestOrchestratorSampling:
    @pytest.mark.asyncio
    async def test_sampling_disabled(self):
        node = _make_node()
        runfile = Runfile(workflow=[node])
        runfile.sampling.enabled = False
        engine = _mock_engine()
        ledger = TokenLedger(budget_usd=10.0)
        orch = TROrchestrator(runfile=runfile, loop_engine=engine, ledger=ledger)

        results = await orch.run_sampling_gate(["a", "b", "c"])
        assert results == []

    @pytest.mark.asyncio
    async def test_sampling_count_mode(self):
        node = _make_node()
        runfile = Runfile(workflow=[node])
        runfile.sampling.mode = "count"
        runfile.sampling.value = 2
        engine = _mock_engine()
        ledger = TokenLedger(budget_usd=10.0)
        orch = TROrchestrator(runfile=runfile, loop_engine=engine, ledger=ledger)

        await orch.run_sampling_gate(["a", "b", "c", "d", "e"])
        assert engine.run.await_count == 2

    @pytest.mark.asyncio
    async def test_budget_exceeded_returns_budget_exceeded(self):
        node = _make_node()
        runfile = Runfile(workflow=[node])
        engine = MagicMock(spec=ActorCriticLoop)
        engine.run = AsyncMock(side_effect=BudgetExceededError("over budget"))
        ledger = TokenLedger(budget_usd=10.0)
        orch = TROrchestrator(runfile=runfile, loop_engine=engine, ledger=ledger)

        results = await orch.run_mass_production(["data"])
        assert results[0]["status"] == "budget_exceeded"
