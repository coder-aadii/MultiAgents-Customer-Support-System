"""Tests for 5B.5: Resilience, Circuit Breakers & Token Bucket Rate Limiting."""

from __future__ import annotations

import pytest

from app.connectors.base import ConnectorCircuitOpenError, ConnectorRateLimitError
from app.connectors.resilience import CircuitBreaker, CircuitState, TokenBucketRateLimiter


@pytest.mark.asyncio
async def test_rate_limiter_throttles_excessive_calls():
    limiter = TokenBucketRateLimiter(capacity=3, refill_rate_per_sec=0.1)

    # 3 allowed
    await limiter.acquire(1)
    await limiter.acquire(1)
    await limiter.acquire(1)

    # 4th fails
    with pytest.raises(ConnectorRateLimitError):
        await limiter.acquire(1)


@pytest.mark.asyncio
async def test_circuit_breaker_trips_on_consecutive_errors():
    breaker = CircuitBreaker(name="test_circuit", failure_threshold=3, recovery_timeout_seconds=5.0)

    assert breaker.state == CircuitState.CLOSED
    await breaker.check_permitted()

    # Record 2 failures -> still closed
    await breaker.record_failure()
    await breaker.record_failure()
    assert breaker.state == CircuitState.CLOSED
    await breaker.check_permitted()

    # 3rd failure -> trips to OPEN
    await breaker.record_failure()
    assert breaker.state == CircuitState.OPEN

    # When OPEN, check_permitted() fails fast without network egress
    with pytest.raises(ConnectorCircuitOpenError):
        await breaker.check_permitted()


@pytest.mark.asyncio
async def test_circuit_breaker_recovers_after_cooldown():
    breaker = CircuitBreaker(
        name="test_recover",
        failure_threshold=2,
        recovery_timeout_seconds=0.05,
        success_threshold=2,
    )

    await breaker.record_failure()
    await breaker.record_failure()
    assert breaker.state == CircuitState.OPEN

    # Sleep past cooldown
    import asyncio
    await asyncio.sleep(0.08)

    # State transitions to HALF_OPEN
    assert breaker.state == CircuitState.HALF_OPEN
    await breaker.check_permitted()

    # Record 2 consecutive successes in HALF_OPEN
    await breaker.record_success()
    assert breaker.state == CircuitState.HALF_OPEN

    await breaker.record_success()
    # Fully recovered to CLOSED
    assert breaker.state == CircuitState.CLOSED

