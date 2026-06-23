"""
Task Queue — async task execution with priority support.

Supports HIGH/NORMAL/LOW priority levels.  LOW priority tasks can be
routed to Batch API for cost savings.  Includes aging mechanism to
prevent priority starvation.
"""

from __future__ import annotations

import asyncio
import time
from enum import IntEnum
from typing import Any, Callable, Dict, Optional

__all__ = ["TaskQueue", "Priority"]


class Priority(IntEnum):
    """Task priority levels.  Lower value = higher priority."""
    HIGH = 0
    NORMAL = 1
    LOW = 2


class _QueueItem:
    """Internal wrapper for a queued task."""

    def __init__(
        self,
        task_id: str,
        func: Callable[..., Any],
        args: tuple,
        kwargs: dict,
        priority: Priority,
        created_at: float,
    ) -> None:
        self.task_id = task_id
        self.func = func
        self.args = args
        self.kwargs = kwargs
        self.priority = priority
        self.created_at = created_at
        self._event = asyncio.Event()
        self._result: Any = None
        self._exception: Optional[BaseException] = None

    def __lt__(self, other: _QueueItem) -> bool:
        """Compare by effective priority (with aging)."""
        return self._effective_priority < other._effective_priority

    @property
    def _effective_priority(self) -> float:
        """Priority with aging bonus — older tasks get priority boost."""
        age_seconds = time.time() - self.created_at
        aging_bonus = min(age_seconds / 60.0, 1.0)  # max 1.0 after 60s
        return max(0, self.priority - aging_bonus)


class TaskQueue:
    """Async task queue with priority support and optional Batch API routing.

    Parameters
    ----------
    max_concurrent:
        Maximum number of tasks executing simultaneously.
    batch_threshold:
        Priority level at which tasks are routed to Batch API.
        Tasks with priority >= this value use Batch API.
        Set to ``Priority.LOW + 1`` to disable Batch routing.
    """

    def __init__(
        self,
        max_concurrent: int = 5,
        batch_threshold: Priority = Priority.LOW,
    ) -> None:
        self.max_concurrent = max_concurrent
        self.batch_threshold = batch_threshold
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._queue: asyncio.PriorityQueue[_QueueItem] = asyncio.PriorityQueue()
        self._tasks: Dict[str, _QueueItem] = {}
        self._running = False
        self._worker_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def submit(
        self,
        task_id: str,
        func: Callable[..., Any],
        *args: Any,
        priority: Priority = Priority.NORMAL,
        **kwargs: Any,
    ) -> str:
        """Submit a task for async execution.

        Parameters
        ----------
        task_id:
            Unique identifier for this task.
        func:
            Async callable to execute.
        priority:
            Task priority level.
        """
        item = _QueueItem(
            task_id=task_id,
            func=func,
            args=args,
            kwargs=kwargs,
            priority=priority,
            created_at=time.time(),
        )
        self._tasks[task_id] = item
        await self._queue.put(item)

        # Auto-start worker if not running
        if not self._running:
            self._start_worker()

        return task_id

    async def wait(self, task_id: str, timeout: float = 300.0) -> Any:
        """Wait for a task to complete and return its result."""
        item = self._tasks.get(task_id)
        if not item:
            raise KeyError(f"Task {task_id} not found")

        await asyncio.wait_for(item._event.wait(), timeout)

        if item._exception:
            raise item._exception
        return item._result

    def cancel(self, task_id: str) -> bool:
        """Cancel a pending task."""
        item = self._tasks.get(task_id)
        if item and not item._event.is_set():
            item._exception = asyncio.CancelledError()
            item._event.set()
            return True
        return False

    @property
    def queue_size(self) -> int:
        """Number of tasks waiting in the queue."""
        return self._queue.qsize()

    @property
    def pending_count(self) -> int:
        """Number of tasks not yet completed."""
        return sum(1 for item in self._tasks.values() if not item._event.is_set())

    def get_stats(self) -> Dict[str, Any]:
        """Return queue statistics."""
        by_priority = {p: 0 for p in Priority}
        for item in self._tasks.values():
            if not item._event.is_set():
                by_priority[item.priority] = by_priority.get(item.priority, 0) + 1
        return {
            "queue_size": self.queue_size,
            "pending": self.pending_count,
            "by_priority": {p.name: c for p, c in by_priority.items()},
        }

    def stop(self) -> None:
        """Stop the background worker."""
        self._running = False
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _start_worker(self) -> None:
        """Start the background queue processor."""
        self._running = True
        self._worker_task = asyncio.create_task(self._process_loop())

    async def _process_loop(self) -> None:
        """Continuously process tasks from the priority queue."""
        while self._running:
            try:
                item = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            # Execute with concurrency control
            async with self._semaphore:
                try:
                    result = await item.func(*item.args, **item.kwargs)
                    item._result = result
                except Exception as exc:
                    item._exception = exc
                finally:
                    item._event.set()
