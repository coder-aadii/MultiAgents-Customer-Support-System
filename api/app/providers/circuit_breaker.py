from __future__ import annotations

import asyncio
import enum
import time
from typing import Any, Awaitable, Callable, TypeVar

from app.providers.base import ProviderCircuitOpenError, is_retryable_error

T = TypeVar("T")


class CircuitState(str, enum.Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Async-safe circuit breaker protecting upstream provider resources.

    States:
    - CLOSED: Normal operation. Every request passes through. Failures increment counter.
              Reaching `failure_threshold` trips the breaker to OPEN.
    - OPEN: Provider is failing. Requests immediately fail with ProviderCircuitOpenError
            without invoking the underlying provider. After `recovery_timeout`, transitions
            to HALF_OPEN.
    - HALF_OPEN: Probing state. Allows a single probe call through. If the probe succeeds,
                 resets to CLOSED. If the probe fails, immediately trips back to OPEN.
    """

    def __init__(
        self,
        name: str = "provider",
        failure_threshold: int = 3,
        recovery_timeout: float = 30.0,
    ) -> None:
        self.name = name
        self.failure_threshold = max(1, failure_threshold)
        self.recovery_timeout = max(0.01, recovery_timeout)

        self._state: CircuitState = CircuitState.CLOSED
        self._consecutive_failures: int = 0
        self._opened_at: float | None = None
        self._probing: bool = False
        self._lock = asyncio.Lock()

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    @property
    def state(self) -> CircuitState:
        """Current state of the circuit breaker, auto-evaluating recovery timeout."""
        if self._state == CircuitState.OPEN and self._opened_at is not None:
            if time.monotonic() - self._opened_at >= self.recovery_timeout:
                return CircuitState.HALF_OPEN
        return self._state

    def is_available(self) -> bool:
        """Check if calls can be attempted without throwing ProviderCircuitOpenError immediately."""
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.HALF_OPEN and not self._probing:
            return True
        return False

    def trip(self) -> None:
        """Manually trip the circuit breaker into OPEN state."""
        self._state = CircuitState.OPEN
        self._opened_at = time.monotonic()
        self._probing = False

    def reset(self) -> None:
        """Manually reset the circuit breaker into CLOSED state."""
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._opened_at = None
        self._probing = False

    async def execute(self, func: Callable[..., Awaitable[T]], *args: Any, **kwargs: Any) -> T:
        """Execute the asynchronous callable through the circuit breaker guard."""
        async with self._lock:
            current_state = self.state
            if current_state == CircuitState.OPEN:
                raise ProviderCircuitOpenError(
                    f"Circuit breaker '{self.name}' is OPEN (opened at {self._opened_at}). Upstream call suppressed."
                )

            # Enforce single probe in HALF_OPEN state
            if current_state == CircuitState.HALF_OPEN:
                if self._probing:
                    raise ProviderCircuitOpenError(
                        f"Circuit breaker '{self.name}' is probing (HALF_OPEN). Concurrent upstream calls suppressed."
                    )
                self._state = CircuitState.HALF_OPEN
                self._probing = True

        # Execute call outside the lock to avoid holding the lock during slow network I/O
        try:
            result = await func(*args, **kwargs)
        except Exception as exc:
            async with self._lock:
                if self._state == CircuitState.HALF_OPEN or is_retryable_error(exc):
                    self._handle_failure()
                else:
                    # Non-retryable error during CLOSED state does not count towards breaker trip
                    self._probing = False
            raise

        async with self._lock:
            self._handle_success()
        return result

    def _handle_success(self) -> None:
        self._consecutive_failures = 0
        self._state = CircuitState.CLOSED
        self._opened_at = None
        self._probing = False

    def _handle_failure(self) -> None:
        self._consecutive_failures += 1
        self._probing = False
        if self._state == CircuitState.HALF_OPEN:
            # Probe failed, trip immediately back to OPEN
            self._state = CircuitState.OPEN
            self._opened_at = time.monotonic()
        elif self._state == CircuitState.CLOSED:
            if self._consecutive_failures >= self.failure_threshold:
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()

