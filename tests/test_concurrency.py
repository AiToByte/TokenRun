"""Concurrency safety tests — verify thread safety, race conditions, and deadlocks."""

import asyncio
import pytest
import threading
from unittest.mock import AsyncMock, MagicMock

from core.ledger import BudgetExceededError, TokenLedger
from core.models import LoopConfig, Runfile, TaskNode, ValidationRule
from core.orchestrator import TROrchestrator
from core.persistence import TaskPersistence
from core.runner import ActorCriticLoop
from gateway.privacy import PrivacyRedactor
from gateway.provider import LLMResponse


def _mock_engine(result_status="success"):
    engine = MagicMock(spec=ActorCriticLoop)
    engine.run = AsyncMock(return_value={
        "status": result_status,
        "final_output": "output",
        "history": [{"iteration": 1, "score": 0.9}],
        "trace": MagicMock(),
    })
    return engine


class TestSQLiteConcurrency:
    @pytest.mark.asyncio
    async def test_concurrent_writes_no_corruption(self, tmp_path):
        """Multiple coroutines writing to SQLite should not corrupt data."""
        p = TaskPersistence(db_path=str(tmp_path / "concurrent.db"))

        async def write_item(i):
            p.save_trace(f"unit-{i}", f"hash-{i}", "completed", {"idx": i}, f"out-{i}")

        # 50 concurrent writes
        await asyncio.gather(*[write_item(i) for i in range(50)])

        traces = p.get_all_traces()
        assert len(traces) == 50

    @pytest.mark.asyncio
    async def test_concurrent_read_write(self, tmp_path):
        """Reads and writes should not interfere."""
        p = TaskPersistence(db_path=str(tmp_path / "rw.db"))
        p.save_trace("pre-existing", "h1", "completed", {}, "out")

        async def read_and_write(i):
            status = p.get_status("pre-existing")
            p.save_trace(f"new-{i}", f"h-{i}", "completed", {}, f"out-{i}")
            return status

        results = await asyncio.gather(*[read_and_write(i) for i in range(20)])
        # All reads should return "completed" (not corrupted)
        assert all(r == "completed" for r in results)
        # All writes should succeed
        assert len(p.get_all_traces()) == 21  # 1 pre-existing + 20 new


class TestLedgerConcurrency:
    def test_concurrent_record_usage_thread_safe(self):
        """Multiple threads recording usage should not lose data."""
        pricing = {"m": {"prompt": 0.001, "completion": 0.001}}
        ledger = TokenLedger(budget_usd=1000.0, pricing_map=pricing)
        errors = []

        def record(i):
            try:
                ledger.record_usage("m", prompt_tokens=10, completion_tokens=5, role="actor")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=record, args=(i,)) for i in range(100)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert ledger.report.call_count == 100
        assert ledger.report.actor_prompt_tokens == 1000  # 10 * 100
        assert ledger.report.actor_completion_tokens == 500  # 5 * 100


class TestSemaphoreConcurrency:
    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrency(self):
        """Orchestrator should respect concurrency limit."""
        node = TaskNode(
            id="n1", name="N1", actor_prompt_template="Do {{ data }}",
            loop_config=LoopConfig(max_attempts=1, exit_criteria=[
                ValidationRule(type="llm_eval", criteria="good")
            ]),
        )
        runfile = Runfile(workflow=[node])

        max_concurrent = 0
        current_concurrent = 0
        lock = asyncio.Lock()

        async def tracking_run(node, data):
            nonlocal max_concurrent, current_concurrent
            async with lock:
                current_concurrent += 1
                max_concurrent = max(max_concurrent, current_concurrent)
            await asyncio.sleep(0.05)
            async with lock:
                current_concurrent -= 1
            return {"status": "success", "final_output": "ok", "history": [], "trace": MagicMock()}

        engine = MagicMock(spec=ActorCriticLoop)
        engine.run = AsyncMock(side_effect=tracking_run)
        ledger = TokenLedger(budget_usd=100.0)
        orch = TROrchestrator(runfile=runfile, loop_engine=engine, ledger=ledger, concurrency=3)

        await orch.run_mass_production(["d1", "d2", "d3", "d4", "d5", "d6"])

        # Max concurrent should be <= 3
        assert max_concurrent <= 3


class TestPrivacyConcurrency:
    def test_concurrent_mask_no_corruption(self):
        """Multiple threads masking should not corrupt the vault."""
        redactor = PrivacyRedactor()
        results = []
        errors = []

        def mask_text(i):
            try:
                text = f"user{i}@test.com"
                masked = redactor.mask(text)
                results.append((text, masked))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=mask_text, args=(i,)) for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        # Each email should be masked
        for original, masked in results:
            assert "@" not in masked or "[[TR_EMAIL_" in masked


class TestPauseBlocksExecution:
    @pytest.mark.asyncio
    async def test_paused_orchestrator_blocks_new_tasks(self):
        """New tasks should block while orchestrator is paused."""
        node = TaskNode(
            id="n1", name="N1", actor_prompt_template="Do {{ data }}",
            loop_config=LoopConfig(max_attempts=1, exit_criteria=[
                ValidationRule(type="llm_eval", criteria="good")
            ]),
        )
        runfile = Runfile(workflow=[node])
        engine = MagicMock(spec=ActorCriticLoop)

        call_count = 0
        async def counting_run(node, data):
            nonlocal call_count
            call_count += 1
            return {"status": "success", "final_output": "ok", "history": [], "trace": MagicMock()}

        engine.run = AsyncMock(side_effect=counting_run)
        ledger = TokenLedger(budget_usd=100.0)
        orch = TROrchestrator(runfile=runfile, loop_engine=engine, ledger=ledger, concurrency=3)

        # Pause before starting
        orch.pause()

        # Start mass production (should block)
        task = asyncio.create_task(orch.run_mass_production(["d1", "d2"]))

        # Give it a moment to try to execute
        await asyncio.sleep(0.1)
        assert call_count == 0  # should be blocked

        # Resume
        orch.resume()
        result = await task
        assert call_count == 2  # now both should execute
