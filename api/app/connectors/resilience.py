from __future__ import annotations

import asyncio
import enum
import time

import structlog

from app.connectors.base import ConnectorCircuitOpenError, ConnectorRateLimitError

logger = structlog.get_logger()


class CircuitState(str, enum.Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Fail-closed circuit breaker protecting upstream enterprise endpoints.

    Invariants:
    - Trips to OPEN after failure_threshold consecutive errors within evaluation window.
    - When OPEN, immediately raises ConnectorCircuitOpenError without network egress.
    - Transitions to HALF_OPEN after recovery_timeout_seconds cooldown.
    - Recovers to CLOSED after success_threshold consecutive successes in HALF_OPEN.
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout_seconds: float = 30.0,
        success_threshold: int = 2,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout_seconds = recovery_timeout_seconds
        self.success_threshold = success_threshold

        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._consecutive_successes = 0
        self._last_state_change = time.monotonic()
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        # Check if OPEN has elapsed its recovery timeout and should become HALF_OPEN
        if self._state == CircuitState.OPEN:
            elapsed = time.monotonic() - self._last_state_change
            if elapsed >= self.recovery_timeout_seconds:
                self._state = CircuitState.HALF_OPEN
                self._consecutive_successes = 0
                logger.info(
                    "Circuit breaker cooldown elapsed, entering HALF_OPEN",
                    circuit=self.name,
                )
        return self._state

    async def check_permitted(self) -> None:
        """Checks if calls are permitted. Raises ConnectorCircuitOpenError if open."""
        async with self._lock:
            current_state = self.state
            if current_state == CircuitState.OPEN:
                raise ConnectorCircuitOpenError(
                    f"Circuit breaker '{self.name}' is OPEN. Upstream requests blocked."
                )

    async def record_success(self) -> None:
        """Records a successful upstream invocation."""
        async with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._consecutive_successes += 1
                if self._consecutive_successes >= self.success_threshold:
                    self._state = CircuitState.CLOSED
                    self._consecutive_failures = 0
                    self._consecutive_successes = 0
                    self._last_state_change = time.monotonic()
                    logger.info("Circuit breaker closed and healthy", circuit=self.name)
            else:
                self._consecutive_failures = 0

    async def record_failure(self) -> None:
        """Records an upstream failure."""
        async with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                self._last_state_change = time.monotonic()
                logger.warning("Circuit breaker re-tripped to OPEN from HALF_OPEN", circuit=self.name)
            else:
                self._consecutive_failures += 1
                if self._consecutive_failures >= self.failure_threshold:
                    self._state = CircuitState.OPEN
                    self._last_state_change = time.monotonic()
                    logger.error(
                        "Circuit breaker tripped to OPEN due to consecutive failures",
                        circuit=self.name,
                        failures=self._consecutive_failures,
                    )


class TokenBucketRateLimiter:
    """In-memory token bucket rate limiter per connector.

    Enforces rate limits on external calls to prevent denial-of-wallet and quota exhaustion.
    """

    def __init__(self, capacity: int = 60, refill_rate_per_sec: float = 1.0) -> None:
        self.capacity = max(1, capacity)
        self.refill_rate = max(0.01, refill_rate_per_sec)
        self._tokens = float(self.capacity)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: int = 1) -> None:
        """Acquires tokens or raises ConnectorRateLimitError."""
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(float(self.capacity), self._tokens + elapsed * self.refill_rate)
            self._last_refill = now

            if self._tokens < tokens:
                raise ConnectorRateLimitError(
                    f"Rate limit exceeded. Available tokens: {self._tokens:.1f}, requested: {tokens}"
                )

            self._tokens -= tokens

