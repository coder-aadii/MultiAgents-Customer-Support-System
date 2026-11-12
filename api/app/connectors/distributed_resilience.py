from __future__ import annotations

import asyncio
import time
from typing import Any

import structlog

from app.config import settings
from app.connectors.base import ConnectorCircuitOpenError, ConnectorRateLimitError
from app.connectors.resilience import CircuitBreaker, CircuitState, TokenBucketRateLimiter

logger = structlog.get_logger()

# Lua script for atomic failure recording and state transition
RECORD_FAILURE_LUA = """-- LUA: RECORD_FAILURE
local key_state = KEYS[1]
local key_failures = KEYS[2]
local key_opened_at = KEYS[3]

local threshold = tonumber(ARGV[1])
local now = tonumber(ARGV[2])

local failures = redis.call('INCR', key_failures)
if failures >= threshold then
    redis.call('SET', key_state, 'open')
    redis.call('SET', key_opened_at, tostring(now))
    return 'open'
else
    return redis.call('GET', key_state) or 'closed'
end
"""

# Lua script to check if execution is permitted, auto-transitioning from OPEN to HALF_OPEN with thundering herd defense
CHECK_PERMITTED_LUA = """-- LUA: CHECK_PERMITTED
local key_state = KEYS[1]
local key_failures = KEYS[2]
local key_opened_at = KEYS[3]
local key_probes = KEYS[4]

local recovery_timeout = tonumber(ARGV[1])
local now = tonumber(ARGV[2])
local max_probes = tonumber(ARGV[3] or '1')

local state = redis.call('GET', key_state) or 'closed'

if state == 'open' then
    local opened_at = tonumber(redis.call('GET', key_opened_at) or '0')
    if (now - opened_at) >= recovery_timeout then
        redis.call('SET', key_state, 'half_open')
        redis.call('SET', key_probes, '1')
        redis.call('EXPIRE', key_probes, math.max(10, math.ceil(recovery_timeout)))
        return 'half_open'
    else
        return 'open'
    end
elseif state == 'half_open' then
    local current_probes = tonumber(redis.call('GET', key_probes) or '0')
    if current_probes < max_probes then
        redis.call('INCR', key_probes)
        return 'half_open'
    else
        return 'open'
    end
end

return state
"""

# Lua script for atomic success recording and recovery
RECORD_SUCCESS_LUA = """-- LUA: RECORD_SUCCESS
local key_state = KEYS[1]
local key_failures = KEYS[2]
local key_opened_at = KEYS[3]
local key_successes = KEYS[4]
local key_probes = KEYS[5]

local success_threshold = tonumber(ARGV[1])

local state = redis.call('GET', key_state) or 'closed'

if state == 'half_open' then
    local succ = redis.call('INCR', key_successes)
    if succ >= success_threshold then
        redis.call('SET', key_state, 'closed')
        redis.call('DEL', key_failures)
        redis.call('DEL', key_opened_at)
        redis.call('DEL', key_successes)
        redis.call('DEL', key_probes)
        return 'closed'
    else
        return 'half_open'
    end
else
    redis.call('DEL', key_failures)
    redis.call('DEL', key_probes)
    return 'closed'
end
"""

# Sliding window rate limiter Lua script with multi-token consumption support
SLIDING_WINDOW_RATE_LIMITER_LUA = """-- LUA: SLIDING_WINDOW_RATE_LIMITER
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local requested_tokens = tonumber(ARGV[4] or '1')
local clear_before = now - window

redis.call('ZREMRANGEBYSCORE', key, '-inf', clear_before)
local current_requests = redis.call('ZCARD', key)

if (current_requests + requested_tokens) <= limit then
    for i = 1, requested_tokens do
        redis.call('ZADD', key, now, tostring(now) .. ':' .. tostring(math.random(1000000)) .. ':' .. tostring(i))
    end
    redis.call('EXPIRE', key, math.ceil(window))
    return 1
else
    return 0
end
"""


class RedisConnectionPool:
    """Singleton async Redis connection pool with connection health management."""

    _instance: Any = None
    _client: Any = None

    @classmethod
    async def get_client(cls) -> Any | None:
        if cls._client is not None:
            return cls._client

        try:
            import redis.asyncio as aioredis

            redis_url = getattr(settings, "MCSS_REDIS_URL", "redis://localhost:6379/0")
            client = aioredis.from_url(
                redis_url,
                decode_responses=True,
                socket_connect_timeout=1.0,
                socket_timeout=1.0,
            )
            # Test ping
            await client.ping()
            cls._client = client
            return cls._client
        except Exception as exc:
            logger.debug("Redis unavailable, using in-memory resilience fallback", error=str(exc))
            return None

    @classmethod
    def set_client(cls, client: Any) -> None:
        """Explicitly set or inject mock/test client."""
        cls._client = client

    @classmethod
    def reset_client(cls) -> None:
        """Reset cached client on connection failure to trigger reconnection."""
        cls._client = None


class RedisDistributedCircuitBreaker:
    """Distributed Redis-backed circuit breaker sharing failure state across all worker containers.

    Transitions:
    - CLOSED: Normal execution across all workers.
    - OPEN: Tripped once failure_threshold is reached globally. All containers immediately fail fast.
    - HALF_OPEN: Cooldown elapsed; single probe permitted globally to defend against thundering herd.
    - CLOSED: Probe succeeds -> resets global failure count.
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout_seconds: float = 30.0,
        success_threshold: int = 2,
        redis_client: Any | None = None,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout_seconds = recovery_timeout_seconds
        self.success_threshold = success_threshold
        self._custom_redis = redis_client
        # In-memory fallback
        self._local_fallback = CircuitBreaker(
            name=name,
            failure_threshold=failure_threshold,
            recovery_timeout_seconds=recovery_timeout_seconds,
            success_threshold=success_threshold,
        )

        self.key_prefix = f"mcss:cb:{name}"
        self.key_state = f"{self.key_prefix}:state"
        self.key_failures = f"{self.key_prefix}:failures"
        self.key_opened_at = f"{self.key_prefix}:opened_at"
        self.key_successes = f"{self.key_prefix}:successes"
        self.key_probes = f"{self.key_prefix}:probes"

    async def _get_redis(self) -> Any | None:
        if self._custom_redis is not None:
            return self._custom_redis
        return await RedisConnectionPool.get_client()

    async def check_permitted(self) -> None:
        """Validates circuit state in Redis. Raises ConnectorCircuitOpenError if OPEN."""
        redis = await self._get_redis()
        if not redis:
            await self._local_fallback.check_permitted()
            return

        now = time.time()
        try:
            state = await redis.eval(
                CHECK_PERMITTED_LUA,
                4,
                self.key_state,
                self.key_failures,
                self.key_opened_at,
                self.key_probes,
                self.recovery_timeout_seconds,
                now,
                1,
            )
            if state == "open":
                raise ConnectorCircuitOpenError(
                    f"Distributed circuit breaker '{self.name}' is OPEN (tripped across worker fleet)"
                )
        except ConnectorCircuitOpenError:
            raise
        except Exception as exc:
            RedisConnectionPool.reset_client()
            logger.warning("Redis circuit check failed, using local fallback", error=str(exc))
            await self._local_fallback.check_permitted()

    async def record_failure(self) -> None:
        """Atomically increments global failure counter and trips to OPEN if threshold reached."""
        redis = await self._get_redis()
        if not redis:
            await self._local_fallback.record_failure()
            return

        now = time.time()
        try:
            state = await redis.eval(
                RECORD_FAILURE_LUA,
                3,
                self.key_state,
                self.key_failures,
                self.key_opened_at,
                self.failure_threshold,
                now,
            )
            if state == "open":
                logger.error(
                    "Distributed circuit breaker tripped to OPEN across worker fleet",
                    circuit=self.name,
                    threshold=self.failure_threshold,
                )
        except Exception as exc:
            RedisConnectionPool.reset_client()
            logger.warning("Redis record_failure failed, using local fallback", error=str(exc))
            await self._local_fallback.record_failure()

    async def record_success(self) -> None:
        """Atomically records successful call, recovering circuit from HALF_OPEN to CLOSED."""
        redis = await self._get_redis()
        if not redis:
            await self._local_fallback.record_success()
            return

        try:
            await redis.eval(
                RECORD_SUCCESS_LUA,
                5,
                self.key_state,
                self.key_failures,
                self.key_opened_at,
                self.key_successes,
                self.key_probes,
                self.success_threshold,
            )
        except Exception as exc:
            RedisConnectionPool.reset_client()
            logger.warning("Redis record_success failed, using local fallback", error=str(exc))
            await self._local_fallback.record_success()

    async def get_state(self) -> CircuitState:
        """Returns current shared circuit state without consuming probe leases."""
        redis = await self._get_redis()
        if not redis:
            return self._local_fallback.state

        try:
            state_str = await redis.get(self.key_state)
            if not state_str:
                return CircuitState.CLOSED

            if state_str == "open":
                opened_at_str = await redis.get(self.key_opened_at)
                if opened_at_str:
                    now = time.time()
                    if (now - float(opened_at_str)) >= self.recovery_timeout_seconds:
                        return CircuitState.HALF_OPEN
            return CircuitState(state_str)
        except Exception:
            return self._local_fallback.state

    async def reset(self) -> None:
        """Force resets circuit breaker to CLOSED across cluster."""
        redis = await self._get_redis()
        if redis:
            try:
                await redis.delete(
                    self.key_state,
                    self.key_failures,
                    self.key_opened_at,
                    self.key_successes,
                    self.key_probes,
                )
            except Exception:
                pass
        self._local_fallback.reset()


class RedisDistributedRateLimiter:
    """Sliding-window distributed rate limiter backed by Redis sorted sets."""

    def __init__(
        self,
        name: str,
        limit: int = 60,
        window_seconds: float = 60.0,
        redis_client: Any | None = None,
    ) -> None:
        self.name = name
        self.limit = max(1, limit)
        self.window_seconds = max(0.1, window_seconds)
        self._custom_redis = redis_client
        self.key = f"mcss:ratelimit:{name}"
        self._local_fallback = TokenBucketRateLimiter(
            capacity=limit,
            refill_rate_per_sec=float(limit) / float(window_seconds),
        )

    async def _get_redis(self) -> Any | None:
        if self._custom_redis is not None:
            return self._custom_redis
        return await RedisConnectionPool.get_client()

    async def acquire(self, tokens: int = 1) -> None:
        """Acquires rate limit tokens globally. Raises ConnectorRateLimitError if exceeded."""
        redis = await self._get_redis()
        if not redis:
            await self._local_fallback.acquire(tokens)
            return

        now = time.time()
        try:
            allowed = await redis.eval(
                SLIDING_WINDOW_RATE_LIMITER_LUA,
                1,
                self.key,
                self.limit,
                self.window_seconds,
                now,
                tokens,
            )
            if not allowed:
                raise ConnectorRateLimitError(
                    f"Global distributed rate limit ({self.limit} req / {self.window_seconds}s) exceeded for '{self.name}'"
                )
        except ConnectorRateLimitError:
            raise
        except Exception as exc:
            RedisConnectionPool.reset_client()
            logger.warning("Redis rate limit check failed, using local fallback", error=str(exc))
            await self._local_fallback.acquire(tokens)

