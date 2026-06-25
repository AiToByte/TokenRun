"""
Resilience — circuit breaker, bulkhead, and retry policy for TokenRun.

Provides three complementary resilience patterns:
- **CircuitBreaker**: Prevents cascading failures by short-circuiting calls
  to a failing service after a threshold of failures.
- **Bulkhead**: Limits concurrent calls to prevent resource exhaustion.
- **RetryPolicy**: Retries transient failures with exponential backoff.

Usage::

    from core.resilience import CircuitBreaker, Bulkhead, RetryPolicy

    breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=60.0)
    result = await breaker.call(llm_provider.request, messages=msgs)

    bulkhead = Bulkhead(max_concurrent=10, max_queue=50)
    result = await bulkhead.execute(task_func, data)

    policy = RetryPolicy(max_retries=3, base_delay=1.0)
    result = await policy.execute(risky_operation, arg1, arg2)
"""

from __future__ import annotations

import asyncio
import time
from enum import Enum
from typing import Any, Callable, Dict, Optional, Set

__all__ = ["CircuitBreaker", "Bulkhead", "RetryPolicy", "CircuitState"]


# ---------------------------------------------------------------------------
# Circuit Breaker
# ---------------------------------------------------------------------------


class CircuitState(str, Enum):
    """Circuit breaker states."""

    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, reject calls
    HALF_OPEN = "half_open"  # Testing recovery


class CircuitBreaker:
    """Async circuit breaker for fault isolation.

    Parameters
    ----------
    failure_threshold:
        Number of consecutive failures before opening the circuit.
    recovery_timeout:
        Seconds to wait before transitioning from OPEN to HALF_OPEN.
    half_open_max:
        Number of probe calls allowed in HALF_OPEN state.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        half_open_max: int = 1,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max = half_open_max

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time = 0.0
        self._half_open_calls = 0
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        """Current circuit state."""
        return self._state

    def get_state(self) -> str:
        """Return current state as string."""
        return self._state.value

    def reset(self) -> None:
        """Reset to closed state."""
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time = 0.0
        self._half_open_calls = 0

    async def call(self, func: Callable, *args: Any, **kwargs: Any) -> Any:
        """Execute a function through the circuit breaker.

        Parameters
        ----------
        func:
            Async callable to execute.
        *args, **kwargs:
            Arguments passed to *func*.

        Returns
        -------
        Any
            The result of *func*.

        Raises
        ------
        CircuitOpenError
            If the circuit is OPEN and recovery timeout has not elapsed.
        """
        # --- Phase 1: check state and reserve slot under lock ---
        async with self._lock:
            # Check if we should transition from OPEN to HALF_OPEN
            if self._state == CircuitState.OPEN:
                if time.time() - self._last_failure_time >= self.recovery_timeout:
                    self._state = CircuitState.HALF_OPEN
                    self._half_open_calls = 0
                else:
                    raise CircuitOpenError(
                        f"断路器已开启，{self.recovery_timeout}s 后重试。"
                    )

            # In HALF_OPEN, limit probe calls
            if self._state == CircuitState.HALF_OPEN:
                if self._half_open_calls >= self.half_open_max:
                    raise CircuitOpenError("断路器半开状态，探测调用已耗尽。")
                self._half_open_calls += 1

        # --- Phase 2: execute the function outside the lock ---
        try:
            result = await func(*args, **kwargs)
        except Exception:
            async with self._lock:
                self._on_failure()
            raise
        else:
            async with self._lock:
                self._on_success()
            return result

    def _on_success(self) -> None:
        """Handle a successful call."""
        if self._state == CircuitState.HALF_OPEN:
            self._success_count += 1
            if self._success_count >= self.half_open_max:
                self._state = CircuitState.CLOSED
                self._failure_count = 0
                self._success_count = 0
        else:
            self._failure_count = 0

    def _on_failure(self) -> None:
        """Handle a failed call."""
        self._failure_count += 1
        self._last_failure_time = time.time()

        if self._state == CircuitState.HALF_OPEN:
            self._state = CircuitState.OPEN
            self._success_count = 0
        elif self._failure_count >= self.failure_threshold:
            self._state = CircuitState.OPEN


class CircuitOpenError(Exception):
    """Raised when the circuit breaker is in OPEN state."""


# ---------------------------------------------------------------------------
# Bulkhead
# ---------------------------------------------------------------------------


class Bulkhead:
    """Async bulkhead for concurrency limiting.

    Parameters
    ----------
    max_concurrent:
        Maximum number of concurrent executions.
    max_queue:
        Maximum number of tasks waiting in the queue.
    """

    def __init__(
        self,
        max_concurrent: int = 10,
        max_queue: int = 100,
    ) -> None:
        self.max_concurrent = max_concurrent
        self.max_queue = max_queue
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._active = 0
        self._queued = 0
        self._rejected = 0
        self._lock = asyncio.Lock()

    async def execute(self, func: Callable, *args: Any, **kwargs: Any) -> Any:
        """Execute a function with concurrency limiting.

        Parameters
        ----------
        func:
            Async callable to execute.
        *args, **kwargs:
            Arguments passed to *func*.

        Returns
        -------
        Any
            The result of *func*.

        Raises
        ------
        BulkheadFullError
            If the queue is full.
        """
        # Atomic check-and-increment under lock
        async with self._lock:
            if self._queued >= self.max_queue:
                self._rejected += 1
                raise BulkheadFullError(
                    f"Bulkhead 队列已满 ({self._queued}/{self.max_queue})。"
                )
            self._queued += 1

        try:
            async with self._semaphore:
                async with self._lock:
                    self._queued -= 1
                    self._active += 1
                try:
                    return await func(*args, **kwargs)
                finally:
                    async with self._lock:
                        self._active -= 1
        except asyncio.CancelledError:
            # Handle cancellation: decrement queued if still waiting
            async with self._lock:
                if self._queued > 0:
                    self._queued -= 1
            raise

    def get_stats(self) -> Dict[str, int]:
        """Return bulkhead statistics.

        Returns
        -------
        dict
            Contains active, queued, rejected, max_concurrent, max_queue.
        """
        return {
            "active": self._active,
            "queued": self._queued,
            "rejected": self._rejected,
            "max_concurrent": self.max_concurrent,
            "max_queue": self.max_queue,
        }


class BulkheadFullError(Exception):
    """Raised when the bulkhead queue is full."""


# ---------------------------------------------------------------------------
# Retry Policy
# ---------------------------------------------------------------------------


class RetryPolicy:
    """Async retry policy with exponential backoff.

    Parameters
    ----------
    max_retries:
        Maximum number of retry attempts.
    base_delay:
        Initial delay in seconds between retries.
    max_delay:
        Maximum delay cap in seconds.
    retryable:
        Set of HTTP status codes that are retryable.  If None, uses
        default set {429, 500, 502, 503, 504}.
    """

    def __init__(
        self,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 30.0,
        retryable: Optional[Set[int]] = None,
    ) -> None:
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.retryable = retryable or {429, 500, 502, 503, 504}

    async def execute(
        self,
        func: Callable,
        *args: Any,
        on_retry: Optional[Callable] = None,
        **kwargs: Any,
    ) -> Any:
        """Execute a function with retry logic.

        Parameters
        ----------
        func:
            Async callable to execute.
        *args, **kwargs:
            Arguments passed to *func*.
        on_retry:
            Optional callback ``(attempt, delay, exception)`` called
            before each retry.

        Returns
        -------
        Any
            The result of *func*.

        Raises
        ------
        Exception
            The last exception after all retries are exhausted.
        """
        last_exception: Optional[Exception] = None

        for attempt in range(self.max_retries + 1):
            try:
                return await func(*args, **kwargs)
            except Exception as exc:
                last_exception = exc

                # Check if the error is retryable
                if not self._is_retryable(exc):
                    raise

                # Don't retry on the last attempt
                if attempt >= self.max_retries:
                    raise

                # Calculate delay with exponential backoff
                delay = min(self.base_delay * (2**attempt), self.max_delay)

                # Respect Retry-After header if present
                retry_after = self._extract_retry_after(exc)
                if retry_after is not None:
                    delay = max(delay, retry_after)

                if on_retry:
                    on_retry(attempt + 1, delay, exc)

                await asyncio.sleep(delay)

        # Should not reach here, but just in case
        raise last_exception  # type: ignore[misc]

    def _is_retryable(self, exc: Exception) -> bool:
        """Check if an exception is retryable."""
        # Check for HTTP status code on the exception itself
        status_code = getattr(exc, "status_code", None)
        if status_code is not None:
            return status_code in self.retryable

        # Check for httpx.HTTPStatusError: status on exc.response.status_code
        response = getattr(exc, "response", None)
        if response is not None:
            resp_status = getattr(response, "status_code", None)
            if resp_status is not None:
                return resp_status in self.retryable

        # Check for common network errors by exception class name
        exc_name = type(exc).__name__
        retryable_names = {
            "TimeoutException",
            "ConnectError",
            "ConnectTimeout",
            "ReadTimeout",
            "WriteTimeout",
            "PoolTimeout",
        }
        return exc_name in retryable_names

    @staticmethod
    def _extract_retry_after(exc: Exception) -> Optional[float]:
        """Extract Retry-After header value from an exception."""
        # httpx stores response headers on the exception
        response = getattr(exc, "response", None)
        if response is not None:
            headers = getattr(response, "headers", {})
            retry_after = headers.get("retry-after") or headers.get("Retry-After")
            if retry_after:
                try:
                    return float(retry_after)
                except (ValueError, TypeError):
                    pass
        return None
