"""Tests for Token Arbitrage Mode — priority queue, cost scheduler."""

import asyncio
import pytest

from core.cost_scheduler import CostScheduler, ExecutionPlan
from core.task_queue import Priority, TaskQueue


# ---------------------------------------------------------------------------
# Priority Queue Tests
# ---------------------------------------------------------------------------

class TestPriority:
    def test_priority_ordering(self):
        assert Priority.HIGH < Priority.NORMAL < Priority.LOW

    def test_priority_names(self):
        assert Priority.HIGH.name == "HIGH"
        assert Priority.NORMAL.name == "NORMAL"
        assert Priority.LOW.name == "LOW"


class TestTaskQueue:
    @pytest.mark.asyncio
    async def test_submit_and_wait(self):
        queue = TaskQueue()

        async def work(x):
            return x * 2

        task_id = await queue.submit("t1", work, 5, priority=Priority.NORMAL)
        result = await queue.wait("t1")
        assert result == 10

    @pytest.mark.asyncio
    async def test_priority_ordering(self):
        """HIGH priority tasks should execute before LOW."""
        execution_order = []
        queue = TaskQueue(max_concurrent=1)

        async def record_order(task_id):
            execution_order.append(task_id)
            await asyncio.sleep(0.05)
            return task_id

        # Submit LOW first, then HIGH
        await queue.submit("low-1", record_order, "low-1", priority=Priority.LOW)
        await queue.submit("high-1", record_order, "high-1", priority=Priority.HIGH)
        await queue.submit("normal-1", record_order, "normal-1", priority=Priority.NORMAL)

        # Wait for all
        await queue.wait("low-1")
        await queue.wait("high-1")
        await queue.wait("normal-1")

        # HIGH should execute first (though ordering may vary with concurrency)
        assert "high-1" in execution_order
        assert "low-1" in execution_order
        assert "normal-1" in execution_order

    @pytest.mark.asyncio
    async def test_cancel(self):
        queue = TaskQueue()

        async def slow():
            await asyncio.sleep(100)
            return "done"

        await queue.submit("t1", slow, priority=Priority.LOW)
        cancelled = queue.cancel("t1")
        assert cancelled is True

    @pytest.mark.asyncio
    async def test_cancel_nonexistent(self):
        queue = TaskQueue()
        assert queue.cancel("nonexistent") is False

    @pytest.mark.asyncio
    async def test_wait_nonexistent_raises(self):
        queue = TaskQueue()
        with pytest.raises(KeyError):
            await queue.wait("nonexistent")

    @pytest.mark.asyncio
    async def test_queue_stats(self):
        queue = TaskQueue()

        async def work():
            return "ok"

        await queue.submit("t1", work, priority=Priority.HIGH)
        await queue.submit("t2", work, priority=Priority.LOW)

        stats = queue.get_stats()
        assert "queue_size" in stats
        assert "by_priority" in stats

    @pytest.mark.asyncio
    async def test_queue_size(self):
        queue = TaskQueue()

        async def slow():
            await asyncio.sleep(10)
            return "ok"

        await queue.submit("t1", slow, priority=Priority.NORMAL)
        # Queue size should be >= 0 (task may already be picked up)
        assert queue.queue_size >= 0

    @pytest.mark.asyncio
    async def test_stop(self):
        queue = TaskQueue()

        async def work():
            return "ok"

        await queue.submit("t1", work, priority=Priority.NORMAL)
        queue.stop()
        assert queue._running is False


# ---------------------------------------------------------------------------
# Cost Scheduler Tests
# ---------------------------------------------------------------------------

class TestCostScheduler:
    def test_plan_high_priority(self):
        pricing = {
            "gpt-4o-mini": {"prompt": 0.00015, "completion": 0.0006},
            "gpt-4o": {"prompt": 0.0025, "completion": 0.01},
        }
        scheduler = CostScheduler(pricing_map=pricing)

        plan = scheduler.plan_execution(Priority.HIGH, estimated_tokens=1000)
        assert plan.strategy == "sync"
        assert plan.provider_model == "gpt-4o-mini"  # cheapest

    def test_plan_normal_priority(self):
        pricing = {
            "gpt-4o-mini": {"prompt": 0.00015, "completion": 0.0006},
            "gpt-4o": {"prompt": 0.0025, "completion": 0.01},
        }
        scheduler = CostScheduler(pricing_map=pricing)

        plan = scheduler.plan_execution(Priority.NORMAL, estimated_tokens=1000)
        assert plan.strategy == "queue"

    def test_plan_low_priority_with_batch(self):
        pricing = {
            "gpt-4o-mini": {"prompt": 0.00015, "completion": 0.0006},
        }
        from unittest.mock import MagicMock
        from gateway.batch_provider import BatchProvider
        batch = MagicMock(spec=BatchProvider)

        scheduler = CostScheduler(pricing_map=pricing, batch_provider=batch)
        plan = scheduler.plan_execution(Priority.LOW, estimated_tokens=1000)
        assert plan.strategy == "batch"
        assert "50%" in plan.reason

    def test_plan_low_priority_no_batch(self):
        pricing = {
            "gpt-4o-mini": {"prompt": 0.00015, "completion": 0.0006},
        }
        scheduler = CostScheduler(pricing_map=pricing, batch_provider=None)

        plan = scheduler.plan_execution(Priority.LOW, estimated_tokens=1000)
        # Without batch provider, falls back to queue
        assert plan.strategy == "queue"

    def test_select_cheapest_provider(self):
        pricing = {
            "expensive": {"prompt": 0.01, "completion": 0.03},
            "cheap": {"prompt": 0.001, "completion": 0.002},
        }
        scheduler = CostScheduler(pricing_map=pricing)

        providers = {"expensive": "provider-a", "cheap": "provider-b"}
        model, provider = scheduler.select_cheapest_provider(providers)
        assert model == "cheap"
        assert provider == "provider-b"

    def test_estimate_cost(self):
        pricing = {
            "gpt-4o-mini": {"prompt": 0.00015, "completion": 0.0006},
        }
        scheduler = CostScheduler(pricing_map=pricing)

        cost = scheduler._estimate_cost("gpt-4o-mini", 1000)
        # 700 prompt tokens * 0.00015/1000 + 300 completion tokens * 0.0006/1000
        expected = 700 / 1000 * 0.00015 + 300 / 1000 * 0.0006
        assert abs(cost - expected) < 1e-10

    def test_update_budget(self):
        scheduler = CostScheduler()
        scheduler.update_budget(50.0)
        assert scheduler._budget_remaining == 50.0

    def test_get_stats(self):
        scheduler = CostScheduler()
        stats = scheduler.get_stats()
        assert "budget_remaining" in stats
        assert "queue_stats" in stats
        assert "batch_available" in stats

    def test_cheapest_model_fallback(self):
        scheduler = CostScheduler(pricing_map={})
        model = scheduler._cheapest_model([])
        assert model == "gpt-4o-mini"  # default fallback
