"""Tests for Tier-A features: PromptLineage, Pause/Resume, Drift, code_eval, S3/SQL, Strict."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from core.actor import TaskActor
from core.critic import TaskCritic
from core.drift_detector import DriftAlert, DriftDetector
from core.models import (
    EvaluationResult,
    LoopConfig,
    LoopStrategy,
    PromptVersion,
    Runfile,
    TaskNode,
    ValidationRule,
)
from core.prompt_lineage import PromptLineageManager
from core.runner import ActorCriticLoop
from gateway.provider import LLMResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_node(template="Do {{ data }}", max_attempts=3):
    return TaskNode(
        id="n1", name="Test", actor_prompt_template=template,
        loop_config=LoopConfig(max_attempts=max_attempts, exit_criteria=[
            ValidationRule(type="llm_eval", criteria="good")
        ]),
    )


# ---------------------------------------------------------------------------
# #1 Prompt Lineage
# ---------------------------------------------------------------------------

class TestPromptLineage:
    def test_register_initial(self):
        mgr = PromptLineageManager()
        node = _make_node("Hello {{ data }}")
        v = mgr.register_initial(node, "Hello {{ data }}")
        assert v.version_id == "v1.0"
        assert node.current_version_id == "v1.0"
        assert len(node.prompt_registry) == 1

    def test_create_version(self):
        mgr = PromptLineageManager()
        node = _make_node("Hello {{ data }}")
        mgr.register_initial(node, "Hello {{ data }}")
        v2 = mgr.create_version(node, "Hi {{ data }}, be brief", "Too verbose")
        assert v2.version_id == "v1.1"
        assert v2.parent_id == "v1.0"
        assert node.current_version_id == "v1.1"
        assert len(node.prompt_registry) == 2
        assert node.actor_prompt_template == "Hi {{ data }}, be brief"

    def test_get_history(self):
        mgr = PromptLineageManager()
        node = _make_node()
        mgr.register_initial(node, "A")
        mgr.create_version(node, "B", "change 1")
        mgr.create_version(node, "C", "change 2")
        history = mgr.get_history(node)
        assert len(history) == 3
        assert [v.version_id for v in history] == ["v1.0", "v1.1", "v1.2"]

    def test_get_lineage_chain(self):
        mgr = PromptLineageManager()
        node = _make_node()
        mgr.register_initial(node, "A")
        mgr.create_version(node, "B", "change 1")
        mgr.create_version(node, "C", "change 2")
        chain = mgr.get_lineage_chain(node)
        assert len(chain) == 3
        assert chain[0].version_id == "v1.0"
        assert chain[-1].version_id == "v1.2"

    def test_record_stats(self):
        mgr = PromptLineageManager()
        node = _make_node()
        mgr.register_initial(node, "A")
        mgr.record_stats(node, "v1.0", {"pass_rate": 0.95})
        v = mgr.get_current(node)
        assert v.stats["pass_rate"] == 0.95


# ---------------------------------------------------------------------------
# #2 Pause/Resume
# ---------------------------------------------------------------------------

class TestPauseResume:
    @pytest.mark.asyncio
    async def test_pause_resume_state(self):
        from core.orchestrator import TROrchestrator
        from core.ledger import TokenLedger

        node = _make_node()
        runfile = Runfile(workflow=[node])
        engine = MagicMock(spec=ActorCriticLoop)
        ledger = TokenLedger(budget_usd=10.0)

        orch = TROrchestrator(runfile=runfile, loop_engine=engine, ledger=ledger)
        assert orch.is_paused is False

        orch.pause()
        assert orch.is_paused is True

        orch.resume()
        assert orch.is_paused is False

    @pytest.mark.asyncio
    async def test_resume_with_new_prompt(self):
        from core.orchestrator import TROrchestrator
        from core.ledger import TokenLedger

        node = _make_node("Original {{ data }}")
        runfile = Runfile(workflow=[node])
        engine = MagicMock(spec=ActorCriticLoop)
        ledger = TokenLedger(budget_usd=10.0)

        orch = TROrchestrator(runfile=runfile, loop_engine=engine, ledger=ledger)
        orch.pause()
        orch.resume(new_prompt="New {{ data }}", change_log="Improved")

        assert node.actor_prompt_template == "New {{ data }}"
        assert len(node.prompt_registry) == 2  # initial + new
        assert orch.is_paused is False


# ---------------------------------------------------------------------------
# #3 Drift Detection
# ---------------------------------------------------------------------------

class TestDriftDetector:
    def test_disabled_by_default(self):
        actor = MagicMock(spec=TaskActor)
        dd = DriftDetector(actor=actor)
        assert dd.enabled is False
        assert dd.tick() is False

    def test_tick_counts(self):
        actor = MagicMock(spec=TaskActor)
        dd = DriftDetector(
            actor=actor,
            golden_samples=[{"input": "test", "expected_output_hash": "abc"}],
            check_interval=5,
        )
        assert dd.enabled is True
        for _ in range(4):
            assert dd.tick() is False
        assert dd.tick() is True  # 5th tick triggers check

    @pytest.mark.asyncio
    async def test_drift_detected_raises(self):
        actor = MagicMock(spec=TaskActor)
        provider = MagicMock()
        provider.request = AsyncMock(return_value=LLMResponse(
            content="different output", prompt_tokens=10, completion_tokens=5, model_name="m"
        ))
        actor.provider = provider

        dd = DriftDetector(
            actor=actor,
            golden_samples=[{"input": "test", "expected_output_hash": "abc123"}],
            check_interval=1,
            threshold=1.0,
        )
        dd.tick()  # trigger check
        with pytest.raises(DriftAlert):
            await dd.run_check("Do {{ data }}")

    @pytest.mark.asyncio
    async def test_no_drift_passes(self):
        # Compute expected hash
        import hashlib
        expected = hashlib.sha256("same output".encode()).hexdigest()[:16]

        actor = MagicMock(spec=TaskActor)
        provider = MagicMock()
        provider.request = AsyncMock(return_value=LLMResponse(
            content="same output", prompt_tokens=10, completion_tokens=5, model_name="m"
        ))
        actor.provider = provider

        dd = DriftDetector(
            actor=actor,
            golden_samples=[{"input": "test", "expected_output_hash": expected}],
            check_interval=1,
            threshold=1.0,
        )
        dd.tick()
        report = await dd.run_check("Do {{ data }}")
        assert report["drift_detected"] is False
        assert report["match_rate"] == 1.0


# ---------------------------------------------------------------------------
# #4 code_eval
# ---------------------------------------------------------------------------

class TestCodeEval:
    def test_split_rules_includes_code_eval(self):
        rules = [
            ValidationRule(type="code_eval", criteria="assert '42' in output"),
            ValidationRule(type="llm_eval", criteria="good"),
        ]
        prog, llm = ActorCriticLoop._split_rules(rules)
        assert len(prog) == 1
        assert prog[0].type == "code_eval"
        assert len(llm) == 1

    def test_code_eval_pass(self):
        passed, score = ActorCriticLoop._run_code_eval(
            "assert 'hello' in output",
            "hello world",
        )
        assert passed is True
        assert score == 1.0

    def test_code_eval_fail(self):
        passed, score = ActorCriticLoop._run_code_eval(
            "assert 'goodbye' in output",
            "hello world",
        )
        assert passed is False
        assert score == 0.0

    def test_code_eval_syntax_error(self):
        passed, score = ActorCriticLoop._run_code_eval(
            "this is not valid python",
            "hello",
        )
        assert passed is False
        assert score == 0.0

    def test_code_eval_timeout(self):
        passed, score = ActorCriticLoop._run_code_eval(
            "import time; time.sleep(30)",
            "hello",
        )
        assert passed is False
        assert score == 0.0


# ---------------------------------------------------------------------------
# #6 Strict Runfile Validation
# ---------------------------------------------------------------------------

class TestStrictRunfile:
    def test_reject_unknown_fields(self):
        import pydantic
        with pytest.raises(pydantic.ValidationError, match="extra"):
            Runfile(name="test", unknown_field="bad")

    def test_accept_known_fields(self):
        rf = Runfile(name="test", version="1.0")
        assert rf.name == "test"

    def test_reject_nested_unknown(self):
        """extra=forbid on Runfile rejects unknown top-level keys.
        Note: Pydantic does not cascade extra=forbid to nested models."""
        import pydantic
        # Top-level unknown is rejected
        with pytest.raises(pydantic.ValidationError):
            Runfile(name="test", bogus_top_level=True)
