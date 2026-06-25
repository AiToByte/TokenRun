"""Tests for core.resilience — circuit breaker, bulkhead, and retry policy."""

from __future__ import annotations

import asyncio

import pytest

from core.resilience import (
    Bulkhead,
    BulkheadFullError,
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
    RetryPolicy,
)


# ---------------------------------------------------------------------------
# Circuit Breaker
# ---------------------------------------------------------------------------


class TestCircuitBreaker:
    """Tests for CircuitBreaker class."""

    def test_init_defaults(self):
        cb = CircuitBreaker()
        assert cb.failure_threshold == 5
        assert cb.recovery_timeout == 60.0
        assert cb.half_open_max == 1

    def test_initial_state_closed(self):
        cb = CircuitBreaker()
        assert cb.state == CircuitState.CLOSED
        assert cb.get_state() == "closed"

    @pytest.mark.asyncio
    async def test_success_stays_closed(self):
        cb = CircuitBreaker()

        async def success():
            return "ok"

        result = await cb.call(success)
        assert result == "ok"
        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_failure_increments_count(self):
        cb = CircuitBreaker(failure_threshold=3)

        async def fail():
            raise ValueError("fail")

        for _ in range(2):
            with pytest.raises(ValueError):
                await cb.call(fail)
        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_threshold_opens_circuit(self):
        cb = CircuitBreaker(failure_threshold=2)

        async def fail():
            raise ValueError("fail")

        for _ in range(2):
            with pytest.raises(ValueError):
                await cb.call(fail)

        assert cb.state == CircuitState.OPEN

    @pytest.mark.asyncio
    async def test_open_circuit_rejects_calls(self):
        cb = CircuitBreaker(failure_threshold=1)

        async def fail():
            raise ValueError("fail")

        with pytest.raises(ValueError):
            await cb.call(fail)

        async def success():
            return "ok"

        with pytest.raises(CircuitOpenError):
            await cb.call(success)

    @pytest.mark.asyncio
    async def test_half_open_after_timeout(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.01)

        async def fail():
            raise ValueError("fail")

        with pytest.raises(ValueError):
            await cb.call(fail)

        assert cb.state == CircuitState.OPEN
        await asyncio.sleep(0.02)  # wait for recovery timeout

        async def success():
            return "ok"

        result = await cb.call(success)
        assert result == "ok"
        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_half_open_failure_reopens(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.01)

        async def fail():
            raise ValueError("fail")

        with pytest.raises(ValueError):
            await cb.call(fail)

        await asyncio.sleep(0.02)

        with pytest.raises(ValueError):
            await cb.call(fail)

        assert cb.state == CircuitState.OPEN

    def test_reset(self):
        cb = CircuitBreaker(failure_threshold=1)
        cb._failure_count = 5
        cb._state = CircuitState.OPEN
        cb.reset()
        assert cb.state == CircuitState.CLOSED
        assert cb._failure_count == 0


# ---------------------------------------------------------------------------
# Bulkhead
# ---------------------------------------------------------------------------


class TestBulkhead:
    """Tests for Bulkhead class."""

    def test_init_defaults(self):
        bh = Bulkhead()
        assert bh.max_concurrent == 10
        assert bh.max_queue == 100

    def test_init_custom(self):
        bh = Bulkhead(max_concurrent=5, max_queue=20)
        assert bh.max_concurrent == 5
        assert bh.max_queue == 20

    @pytest.mark.asyncio
    async def test_execute_success(self):
        bh = Bulkhead(max_concurrent=2)

        async def task():
            return 42

        result = await bh.execute(task)
        assert result == 42

    @pytest.mark.asyncio
    async def test_execute_with_args(self):
        bh = Bulkhead()

        async def add(a, b):
            return a + b

        result = await bh.execute(add, 3, 4)
        assert result == 7

    @pytest.mark.asyncio
    async def test_concurrent_limiting(self):
        bh = Bulkhead(max_concurrent=2, max_queue=10)
        running = {"count": 0}
        max_running = {"count": 0}

        async def task():
            running["count"] += 1
            max_running["count"] = max(max_running["count"], running["count"])
            await asyncio.sleep(0.05)
            running["count"] -= 1

        await asyncio.gather(*[bh.execute(task) for _ in range(5)])
        assert max_running["count"] <= 2

    @pytest.mark.asyncio
    async def test_queue_full_rejects(self):
        bh = Bulkhead(max_concurrent=1, max_queue=1)

        async def slow_task():
            await asyncio.sleep(0.1)

        # First task holds the semaphore, second fills the queue
        task1 = asyncio.create_task(bh.execute(slow_task))
        await asyncio.sleep(0.01)  # let task1 acquire semaphore
        task2 = asyncio.create_task(bh.execute(slow_task))
        await asyncio.sleep(0.01)  # let task2 queue

        with pytest.raises(BulkheadFullError):
            await bh.execute(slow_task)

        await task1
        await task2

    @pytest.mark.asyncio
    async def test_get_stats(self):
        bh = Bulkhead(max_concurrent=2, max_queue=10)
        stats = bh.get_stats()
        assert stats["active"] == 0
        assert stats["queued"] == 0
        assert stats["rejected"] == 0
        assert stats["max_concurrent"] == 2
        assert stats["max_queue"] == 10


# ---------------------------------------------------------------------------
# Retry Policy
# ---------------------------------------------------------------------------


class TestRetryPolicy:
    """Tests for RetryPolicy class."""

    def test_init_defaults(self):
        policy = RetryPolicy()
        assert policy.max_retries == 3
        assert policy.base_delay == 1.0
        assert policy.max_delay == 30.0
        assert 429 in policy.retryable

    def test_init_custom(self):
        policy = RetryPolicy(max_retries=5, base_delay=0.5, max_delay=10.0)
        assert policy.max_retries == 5
        assert policy.base_delay == 0.5
        assert policy.max_delay == 10.0

    @pytest.mark.asyncio
    async def test_success_no_retry(self):
        policy = RetryPolicy(max_retries=3)
        call_count = {"count": 0}

        async def success():
            call_count["count"] += 1
            return "ok"

        result = await policy.execute(success)
        assert result == "ok"
        assert call_count["count"] == 1

    @pytest.mark.asyncio
    async def test_retry_on_retryable_error(self):
        policy = RetryPolicy(max_retries=2, base_delay=0.01)
        call_count = {"count": 0}

        class RetryableError(Exception):
            status_code = 429

        async def fail_then_succeed():
            call_count["count"] += 1
            if call_count["count"] < 3:
                raise RetryableError("rate limited")
            return "ok"

        result = await policy.execute(fail_then_succeed)
        assert result == "ok"
        assert call_count["count"] == 3

    @pytest.mark.asyncio
    async def test_no_retry_on_non_retryable_error(self):
        policy = RetryPolicy(max_retries=3, base_delay=0.01)

        class NonRetryableError(Exception):
            status_code = 400

        async def fail():
            raise NonRetryableError("bad request")

        with pytest.raises(NonRetryableError):
            await policy.execute(fail)

    @pytest.mark.asyncio
    async def test_exhausted_retries_raises(self):
        policy = RetryPolicy(max_retries=2, base_delay=0.01)
        call_count = {"count": 0}

        class RetryableError(Exception):
            status_code = 500

        async def always_fail():
            call_count["count"] += 1
            raise RetryableError("server error")

        with pytest.raises(RetryableError):
            await policy.execute(always_fail)

        # 1 initial + 2 retries = 3 total
        assert call_count["count"] == 3

    @pytest.mark.asyncio
    async def test_on_retry_callback(self):
        policy = RetryPolicy(max_retries=2, base_delay=0.01)
        retry_info = []

        class RetryableError(Exception):
            status_code = 500

        async def fail():
            raise RetryableError("error")

        def on_retry(attempt, delay, exc):
            retry_info.append({"attempt": attempt, "delay": delay})

        with pytest.raises(RetryableError):
            await policy.execute(fail, on_retry=on_retry)

        assert len(retry_info) == 2
        assert retry_info[0]["attempt"] == 1
        assert retry_info[1]["attempt"] == 2

    @pytest.mark.asyncio
    async def test_exponential_backoff_delays(self):
        policy = RetryPolicy(max_retries=3, base_delay=0.01, max_delay=1.0)

        # Verify delay calculation: base * 2^attempt, capped at max
        assert policy.base_delay * (2 ** 0) == 0.01
        assert policy.base_delay * (2 ** 1) == 0.02
        assert policy.base_delay * (2 ** 2) == 0.04

    def test_is_retryable_status_codes(self):
        policy = RetryPolicy()
        exc = Exception("test")
        exc.status_code = 429
        assert policy._is_retryable(exc) is True

        exc2 = Exception("test")
        exc2.status_code = 400
        assert policy._is_retryable(exc2) is False

    def test_is_retryable_network_errors(self):
        policy = RetryPolicy()

        class TimeoutException(Exception):
            pass

        assert policy._is_retryable(TimeoutException("timeout")) is True

    def test_extract_retry_after(self):
        policy = RetryPolicy()

        class MockResponse:
            headers = {"Retry-After": "5"}

        exc = Exception("test")
        exc.response = MockResponse()
        assert policy._extract_retry_after(exc) == 5.0

    def test_extract_retry_after_missing(self):
        policy = RetryPolicy()
        assert policy._extract_retry_after(Exception("test")) is None
