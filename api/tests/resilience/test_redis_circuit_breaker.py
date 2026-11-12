import asyncio
import pytest

from app.connectors.base import ConnectorCircuitOpenError, ConnectorRateLimitError
from app.connectors.distributed_resilience import (
    RedisDistributedCircuitBreaker,
    RedisDistributedRateLimiter,
)
from app.connectors.resilience import CircuitState


class MockRedis:
    """In-memory mock Redis client supporting EVAL, GET, SET, DEL, INCR for unit tests."""

    def __init__(self):
        self.store = {}
        self.zsets = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value):
        self.store[key] = str(value)
        return True

    async def delete(self, *keys):
        for k in keys:
            self.store.pop(k, None)
            self.zsets.pop(k, None)
        return True

    async def eval(self, script, numkeys, *args):
        keys = list(args[:numkeys])
        script_args = list(args[numkeys:])

        # Simulate RECORD_SUCCESS_LUA
        if "LUA: RECORD_SUCCESS" in script or ("key_successes" in script and "threshold" in script):
            key_state = keys[0]
            key_failures = keys[1]
            key_opened_at = keys[2]
            key_successes = keys[3]
            key_probes = keys[4] if len(keys) > 4 else None
            threshold = int(script_args[0])

            state = self.store.get(key_state, "closed")
            if state == "half_open":
                succ = int(self.store.get(key_successes, 0)) + 1
                self.store[key_successes] = str(succ)
                if succ >= threshold:
                    self.store[key_state] = "closed"
                    self.store.pop(key_failures, None)
                    self.store.pop(key_opened_at, None)
                    self.store.pop(key_successes, None)
                    if key_probes:
                        self.store.pop(key_probes, None)
                    return "closed"
                return "half_open"
            else:
                self.store.pop(key_failures, None)
                if key_probes:
                    self.store.pop(key_probes, None)
                return "closed"

        # Simulate CHECK_PERMITTED_LUA
        if "LUA: CHECK_PERMITTED" in script or ("recovery_timeout" in script and "max_probes" in script):
            key_state = keys[0]
            key_opened_at = keys[2]
            key_probes = keys[3] if len(keys) > 3 else "mock_probes"
            timeout = float(script_args[0])
            now = float(script_args[1])
            max_probes = int(script_args[2]) if len(script_args) > 2 else 1

            state = self.store.get(key_state, "closed")
            if state == "open":
                opened_at = float(self.store.get(key_opened_at, 0))
                if (now - opened_at) >= timeout:
                    self.store[key_state] = "half_open"
                    self.store[key_probes] = "1"
                    return "half_open"
                return "open"
            elif state == "half_open":
                current_probes = int(self.store.get(key_probes, 0))
                if current_probes < max_probes:
                    self.store[key_probes] = str(current_probes + 1)
                    return "half_open"
                return "open"
            return state

        # Simulate RECORD_FAILURE_LUA
        if "LUA: RECORD_FAILURE" in script or ("INCR" in script and "threshold" in script):
            key_state = keys[0]
            key_failures = keys[1]
            key_opened_at = keys[2]
            threshold = int(script_args[0])
            now = float(script_args[1])

            failures = int(self.store.get(key_failures, 0)) + 1
            self.store[key_failures] = str(failures)
            if failures >= threshold:
                self.store[key_state] = "open"
                self.store[key_opened_at] = str(now)
                return "open"
            return self.store.get(key_state, "closed")

        # Simulate SLIDING_WINDOW_RATE_LIMITER_LUA
        if "LUA: SLIDING_WINDOW_RATE_LIMITER" in script or "ZREMRANGEBYSCORE" in script:
            key = keys[0]
            limit = int(script_args[0])
            window = float(script_args[1])
            now = float(script_args[2])
            requested_tokens = int(script_args[3]) if len(script_args) > 3 else 1
            clear_before = now - window

            if key not in self.zsets:
                self.zsets[key] = []

            # Filter old requests
            self.zsets[key] = [score for score in self.zsets[key] if score >= clear_before]

            if (len(self.zsets[key]) + requested_tokens) <= limit:
                for _ in range(requested_tokens):
                    self.zsets[key].append(now)
                return 1
            return 0

        return None


@pytest.mark.asyncio
async def test_distributed_circuit_breaker_trips_across_workers():
    shared_redis = MockRedis()

    worker_a = RedisDistributedCircuitBreaker(
        name="orders_api",
        failure_threshold=3,
        recovery_timeout_seconds=5.0,
        redis_client=shared_redis,
    )
    worker_b = RedisDistributedCircuitBreaker(
        name="orders_api",
        failure_threshold=3,
        recovery_timeout_seconds=5.0,
        redis_client=shared_redis,
    )

    # Initial state is CLOSED
    await worker_a.check_permitted()
    await worker_b.check_permitted()
    assert await worker_a.get_state() == CircuitState.CLOSED
    assert await worker_b.get_state() == CircuitState.CLOSED

    # Worker A records 2 failures (below threshold)
    await worker_a.record_failure()
    await worker_a.record_failure()
    await worker_b.check_permitted()  # Still permitted

    # Worker A records 3rd failure -> Trips to OPEN globally in Redis
    await worker_a.record_failure()
    assert await worker_a.get_state() == CircuitState.OPEN

    # Worker B immediately trips without sending network requests
    with pytest.raises(ConnectorCircuitOpenError) as exc:
        await worker_b.check_permitted()
    assert "is OPEN (tripped across worker fleet)" in str(exc.value)

    # Simulate recovery cooldown elapsed
    import time
    shared_redis.store["mcss:cb:orders_api:opened_at"] = str(time.time() - 100.0)

    # Both workers see HALF_OPEN probe state
    assert await worker_b.get_state() == CircuitState.HALF_OPEN
    await worker_b.check_permitted()

    # Successful probe resets circuit to CLOSED
    await worker_b.record_success()
    await worker_b.record_success()
    assert await worker_a.get_state() == CircuitState.CLOSED
    assert await worker_b.get_state() == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_distributed_circuit_breaker_in_memory_fallback():
    # When Redis client is None, operates cleanly using in-memory fallback
    cb = RedisDistributedCircuitBreaker(
        name="isolated_breaker",
        failure_threshold=2,
        recovery_timeout_seconds=1.0,
        redis_client=None,
    )

    await cb.check_permitted()
    await cb.record_failure()
    await cb.record_failure()

    # Tripped in memory
    with pytest.raises(ConnectorCircuitOpenError):
        await cb.check_permitted()


@pytest.mark.asyncio
async def test_distributed_rate_limiter_global_enforcement():
    shared_redis = MockRedis()

    limiter_a = RedisDistributedRateLimiter(
        name="global_api",
        limit=3,
        window_seconds=10.0,
        redis_client=shared_redis,
    )
    limiter_b = RedisDistributedRateLimiter(
        name="global_api",
        limit=3,
        window_seconds=10.0,
        redis_client=shared_redis,
    )

    # Consume 2 from instance A and 1 from instance B
    await limiter_a.acquire(1)
    await limiter_b.acquire(1)
    await limiter_a.acquire(1)

    # 4th request from instance B exceeds global shared limit of 3
    with pytest.raises(ConnectorRateLimitError) as exc:
        await limiter_b.acquire(1)
    assert "Global distributed rate limit" in str(exc.value)


@pytest.mark.asyncio
async def test_distributed_rate_limiter_multi_token_consumption():
    shared_redis = MockRedis()
    limiter = RedisDistributedRateLimiter(
        name="batch_api",
        limit=5,
        window_seconds=10.0,
        redis_client=shared_redis,
    )
    # Acquire 3 tokens
    await limiter.acquire(3)
    # Acquiring 3 more tokens should fail because 3 + 3 = 6 > 5
    with pytest.raises(ConnectorRateLimitError):
        await limiter.acquire(3)
    # Acquiring 2 tokens succeeds because 3 + 2 = 5 <= 5
    await limiter.acquire(2)


@pytest.mark.asyncio
async def test_thundering_herd_prevention_in_half_open():
    shared_redis = MockRedis()
    worker_a = RedisDistributedCircuitBreaker(
        name="probe_api",
        failure_threshold=2,
        recovery_timeout_seconds=0.1,
        success_threshold=1,
        redis_client=shared_redis,
    )
    worker_b = RedisDistributedCircuitBreaker(
        name="probe_api",
        failure_threshold=2,
        recovery_timeout_seconds=0.1,
        success_threshold=1,
        redis_client=shared_redis,
    )

    # Trip breaker to OPEN
    await worker_a.record_failure()
    await worker_a.record_failure()
    assert await worker_a.get_state() == CircuitState.OPEN

    # Wait for cooldown
    await asyncio.sleep(0.15)

    # Worker A gets the single trial probe
    await worker_a.check_permitted()

    # Worker B is prevented from thundering into recovering upstream
    with pytest.raises(ConnectorCircuitOpenError):
        await worker_b.check_permitted()

    # Once Worker A records success, circuit recovers to CLOSED for both
    await worker_a.record_success()
    assert await worker_a.get_state() == CircuitState.CLOSED
    await worker_b.check_permitted()

