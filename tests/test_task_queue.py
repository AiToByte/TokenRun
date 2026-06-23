"""Tests for core.task_queue — async task execution."""

import pytest
import asyncio
from core.task_queue import TaskQueue


class TestTaskQueue:
    @pytest.mark.asyncio
    async def test_submit_and_wait(self):
        queue = TaskQueue()

        async def work(x):
            return x * 2

        task_id = await queue.submit("t1", work, 5)
        assert task_id == "t1"

        result = await queue.wait("t1")
        assert result == 10

    @pytest.mark.asyncio
    async def test_cancel(self):
        queue = TaskQueue()

        async def slow():
            await asyncio.sleep(100)
            return "done"

        await queue.submit("t1", slow)
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
    async def test_multiple_tasks(self):
        queue = TaskQueue()

        async def work(x):
            await asyncio.sleep(0.01)
            return x

        for i in range(5):
            await queue.submit(f"t{i}", work, i)

        results = []
        for i in range(5):
            r = await queue.wait(f"t{i}")
            results.append(r)

        assert results == [0, 1, 2, 3, 4]
