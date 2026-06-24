"""Tests for v2 iteration — quality circuit breaker, drift halt, consensus, version tree, replay."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from core.ledger import TokenLedger
from core.models import (
    EvaluationResult,
    Fingerprint,
    LoopConfig,
    LoopStrategy,
    Runfile,
    TaskNode,
    ValidationRule,
)
from core.orchestrator import TROrchestrator
from core.runner import ActorCriticLoop
from core.drift_detector import DriftAlert


def _mock_engine(score=0.9, passed=True):
    engine = MagicMock(spec=ActorCriticLoop)
    engine.run = AsyncMock(return_value={
        "status": "success" if passed else "exhausted",
        "final_output": "output",
        "history": [{"iteration": 1, "score": score, "passed": passed, "critique": None}],
        "trace": MagicMock(),
    })
    engine.verify_fingerprint = MagicMock(return_value=True)
    return engine


def _make_node():
    return TaskNode(
        id="n1", name="N1", actor_prompt_template="Do {{ data }}",
        loop_config=LoopConfig(max_attempts=1, exit_criteria=[
            ValidationRule(type="llm_eval", criteria="good")
        ]),
    )


# ---------------------------------------------------------------------------
# Quality Circuit Breaker
# ---------------------------------------------------------------------------

class TestQualityCircuitBreaker:
    @pytest.mark.asyncio
    async def test_consecutive_low_scores_halt(self):
        """Quality circuit breaker should halt after N consecutive low scores."""
        node = _make_node()
        runfile = Runfile(workflow=[node])

        engine = MagicMock(spec=ActorCriticLoop)
        engine.run = AsyncMock(return_value={
            "status": "exhausted",
            "final_output": "bad",
            "history": [{"iteration": 1, "score": 0.3, "passed": False, "critique": "bad"}],
            "trace": MagicMock(),
        })

        ledger = TokenLedger(budget_usd=100.0)
        orch = TROrchestrator(
            runfile=runfile, loop_engine=engine, ledger=ledger,
            quality_threshold=0.6, quality_window=3,
        )

        results = await orch.run_mass_production(["d1", "d2", "d3", "d4"])
        # After 3 consecutive low scores, 4th should be quality_halted
        statuses = [r["status"] for r in results]
        assert "quality_halted" in statuses

    @pytest.mark.asyncio
    async def test_high_scores_no_halt(self):
        """High scores should not trigger quality halt."""
        node = _make_node()
        runfile = Runfile(workflow=[node])

        engine = _mock_engine(score=0.9, passed=True)
        ledger = TokenLedger(budget_usd=100.0)
        orch = TROrchestrator(
            runfile=runfile, loop_engine=engine, ledger=ledger,
            quality_threshold=0.6, quality_window=3,
        )

        results = await orch.run_mass_production(["d1", "d2", "d3"])
        assert all(r["status"] == "success" for r in results)


# ---------------------------------------------------------------------------
# Drift Auto-Halt
# ---------------------------------------------------------------------------

class TestDriftAutoHalt:
    @pytest.mark.asyncio
    async def test_drift_halt_stops_execution(self):
        """DriftAlert with halt action should stop execution."""
        from core.drift_detector import DriftDetector

        node = _make_node()
        runfile = Runfile(workflow=[node])

        engine = _mock_engine()

        drift_detector = MagicMock(spec=DriftDetector)
        drift_detector.tick = MagicMock(return_value=True)
        drift_detector.run_check = AsyncMock(side_effect=DriftAlert("Drift detected!"))

        ledger = TokenLedger(budget_usd=100.0)
        orch = TROrchestrator(
            runfile=runfile, loop_engine=engine, ledger=ledger,
            drift_detector=drift_detector, drift_action="halt",
        )

        results = await orch.run_mass_production(["d1", "d2"])
        statuses = [r["status"] for r in results]
        assert "drift_halted" in statuses

    @pytest.mark.asyncio
    async def test_drift_warn_continues(self):
        """DriftAlert with warn action should continue execution."""
        from core.drift_detector import DriftDetector

        node = _make_node()
        runfile = Runfile(workflow=[node])

        engine = _mock_engine()

        drift_detector = MagicMock(spec=DriftDetector)
        drift_detector.tick = MagicMock(return_value=True)
        drift_detector.run_check = AsyncMock(side_effect=DriftAlert("Drift detected!"))

        ledger = TokenLedger(budget_usd=100.0)
        orch = TROrchestrator(
            runfile=runfile, loop_engine=engine, ledger=ledger,
            drift_detector=drift_detector, drift_action="warn",
        )

        results = await orch.run_mass_production(["d1", "d2"])
        # Should continue despite drift alert
        assert len(results) == 2


# ---------------------------------------------------------------------------
# Consensus Validation (model definition)
# ---------------------------------------------------------------------------

class TestConsensusConfig:
    def test_consensus_fields_in_loop_config(self):
        config = LoopConfig(
            consensus_models=["gpt-4o-mini", "deepseek-chat"],
            consensus_threshold=0.5,
        )
        assert config.consensus_models == ["gpt-4o-mini", "deepseek-chat"]
        assert config.consensus_threshold == 0.5

    def test_consensus_defaults_empty(self):
        config = LoopConfig()
        assert config.consensus_models == []
        assert config.consensus_threshold == 0.5


# ---------------------------------------------------------------------------
# MCP Resource Type
# ---------------------------------------------------------------------------

class TestMCPResourceType:
    def test_mcp_tool_type_exists(self):
        from core.models import ResourceType
        assert ResourceType.MCP_TOOL == "mcp_tool"


# ---------------------------------------------------------------------------
# Version Tree Endpoint
# ---------------------------------------------------------------------------

class TestVersionTree:
    @pytest.mark.asyncio
    async def test_version_tree_empty(self):
        from httpx import ASGITransport, AsyncClient
        from api.main import app, _active_missions

        _active_missions.clear()
        _active_missions["test-1"] = {"lineage": []}

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/missions/test-1/version-tree")
            assert resp.status_code == 200
            data = resp.json()
            assert data["nodes"] == []
            assert data["edges"] == []

        _active_missions.clear()

    @pytest.mark.asyncio
    async def test_version_tree_with_lineage(self):
        from httpx import ASGITransport, AsyncClient
        from api.main import app, _active_missions

        _active_missions.clear()
        _active_missions["test-2"] = {
            "lineage": [
                {"version_id": "v1.0", "template": "Hello", "change_log": "init", "stats": {}, "parent_id": None},
                {"version_id": "v1.1", "template": "Hello v2", "change_log": "improved", "stats": {"pass_rate": 0.9}, "parent_id": "v1.0"},
            ]
        }

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/missions/test-2/version-tree")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["nodes"]) == 2
            assert len(data["edges"]) == 1
            assert data["edges"][0]["from"] == "v1.0"
            assert data["edges"][0]["to"] == "v1.1"

        _active_missions.clear()


# ---------------------------------------------------------------------------
# Replay Endpoint
# ---------------------------------------------------------------------------

class TestReplay:
    @pytest.mark.asyncio
    async def test_replay_queues_request(self):
        from httpx import ASGITransport, AsyncClient
        from api.main import app, _active_missions

        _active_missions.clear()
        _active_missions["test-3"] = {"result": {"results": []}}

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/missions/test-3/replay?iteration=2")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "replay_queued"
            assert _active_missions["test-3"]["replay_request"]["from_iteration"] == 2

        _active_missions.clear()

    @pytest.mark.asyncio
    async def test_replay_no_results_fails(self):
        from httpx import ASGITransport, AsyncClient
        from api.main import app, _active_missions

        _active_missions.clear()
        _active_missions["test-4"] = {"result": {}}

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/missions/test-4/replay?iteration=0")
            assert resp.status_code == 400

        _active_missions.clear()
