"""
Task Queue — async task execution via Celery or in-process fallback.

For production, configure Celery with Redis/RabbitMQ.  The in-process
fallback uses asyncio for development and testing.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, Optional

__all__ = ["TaskQueue"]


class TaskQueue:
    """Async task queue with optional Celery backend.

    Parameters
    ----------
    backend:
        ``"asyncio"`` (default, in-process) or ``"celery"`` (requires
        Celery + Redis/RabbitMQ).
    celery_app:
        Required when backend is ``"celery"``.
    """

    def __init__(
        self,
        backend: str = "asyncio",
        celery_app: Optional[Any] = None,
    ) -> None:
        self.backend = backend
        self._celery = celery_app
        self._tasks: Dict[str, asyncio.Task] = {}

    async def submit(
        self,
        task_id: str,
        func: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> str:
        """Submit a task for async execution.

        Returns the task_id for tracking.
        """
        if self.backend == "celery" and self._celery:
            self._celery.send_task(func.__name__, args=args, kwargs=kwargs)
        else:
            task = asyncio.create_task(func(*args, **kwargs))
            self._tasks[task_id] = task
        return task_id

    async def wait(self, task_id: str, timeout: float = 300.0) -> Any:
        """Wait for a task to complete and return its result."""
        task = self._tasks.get(task_id)
        if task:
            return await asyncio.wait_for(task, timeout)
        raise KeyError(f"Task {task_id} not found")

    def cancel(self, task_id: str) -> bool:
        """Cancel a running task."""
        task = self._tasks.get(task_id)
        if task and not task.done():
            task.cancel()
            return True
        return False
