from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


class LLMMessage(BaseModel):
    """Typed chat message for LLM interactions."""

    model_config = ConfigDict(frozen=True)

    role: Literal["system", "user", "assistant"] | str
    content: str


class LLMRequest(BaseModel):
    """Typed request for LLM chat completion."""

    model_config = ConfigDict(frozen=True)

    messages: list[LLMMessage]
    model: str | None = None
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_tokens: int = Field(default=512, ge=1, le=4096)
    response_format: dict[str, Any] | None = None
    timeout: float = Field(default=15.0, gt=0.0, le=120.0)


class LLMResponse(BaseModel):
    """Typed response from LLM chat completion."""

    model_config = ConfigDict(frozen=True)

    content: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    model: str = ""


# --- Provider Error Hierarchy ---


class ProviderError(Exception):
    """Base exception for all external provider errors."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.retryable = retryable


class ProviderTimeoutError(ProviderError):
    """Raised when provider call times out."""

    def __init__(self, message: str = "Provider request timed out", *, status_code: int | None = 408) -> None:
        super().__init__(message, status_code=status_code, retryable=True)


class ProviderConnectionError(ProviderError):
    """Raised on network failure or transient provider HTTP 5xx error."""

    def __init__(
        self,
        message: str = "Provider connection failed",
        *,
        status_code: int | None = 503,
    ) -> None:
        super().__init__(message, status_code=status_code, retryable=True)


class ProviderRateLimitError(ProviderError):
    """Raised when provider returns HTTP 429 rate limit."""

    def __init__(
        self,
        message: str = "Provider rate limit exceeded",
        *,
        status_code: int | None = 429,
    ) -> None:
        super().__init__(message, status_code=status_code, retryable=True)


class ProviderAuthError(ProviderError):
    """Raised on invalid credentials or authentication failure (HTTP 401/403)."""

    def __init__(
        self,
        message: str = "Provider authentication failed",
        *,
        status_code: int | None = 401,
    ) -> None:
        super().__init__(message, status_code=status_code, retryable=False)


class ProviderBadRequestError(ProviderError):
    """Raised on invalid request parameters or client payload (HTTP 400)."""

    def __init__(
        self,
        message: str = "Provider rejected request as invalid",
        *,
        status_code: int | None = 400,
    ) -> None:
        super().__init__(message, status_code=status_code, retryable=False)


class ProviderResponseError(ProviderError):
    """Raised when provider returns empty or unparseable response structure."""

    def __init__(
        self,
        message: str = "Malformed or unexpected response from provider",
        *,
        status_code: int | None = 502,
    ) -> None:
        super().__init__(message, status_code=status_code, retryable=False)


class ProviderCircuitOpenError(ProviderError):
    """Raised when the circuit breaker is open, preventing upstream calls."""

    def __init__(
        self,
        message: str = "Provider circuit breaker is open; upstream calls suspended",
    ) -> None:
        super().__init__(message, status_code=503, retryable=False)


def is_retryable_error(exc: Exception) -> bool:
    """Classify whether an exception represents a transient, retryable failure."""
    if isinstance(exc, ProviderError):
        return exc.retryable
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return True
    try:
        import httpx

        if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)):
            return True
    except ImportError:
        pass
    return False


# --- Metrics Hooks ---


class ProviderMetricsHook(Protocol):
    """Protocol for observing provider request latency, outcomes, and token usage."""

    def on_request_start(self, provider: str, operation: str, model: str) -> None: ...

    def on_request_success(
        self,
        provider: str,
        operation: str,
        model: str,
        duration_ms: float,
        tokens: dict[str, int],
    ) -> None: ...

    def on_request_failure(
        self,
        provider: str,
        operation: str,
        model: str,
        duration_ms: float,
        error: Exception,
    ) -> None: ...


class NoOpMetricsHook:
    """Default no-op implementation of ProviderMetricsHook."""

    def on_request_start(self, provider: str, operation: str, model: str) -> None:
        pass

    def on_request_success(
        self,
        provider: str,
        operation: str,
        model: str,
        duration_ms: float,
        tokens: dict[str, int],
    ) -> None:
        pass

    def on_request_failure(
        self,
        provider: str,
        operation: str,
        model: str,
        duration_ms: float,
        error: Exception,
    ) -> None:
        pass


# --- Abstract Providers ---


class EmbeddingProvider(ABC):
    """Abstract interface for generating vector embeddings."""

    dimensions: int = 1536

    @abstractmethod
    async def embed_text(self, text: str) -> list[float]:
        """Generate embedding vector for a single text chunk."""
        raise NotImplementedError

    @abstractmethod
    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embedding vectors for a batch of text chunks."""
        raise NotImplementedError


class LLMProvider(ABC):
    """Abstract interface for LLM chat completion."""

    @abstractmethod
    async def chat_complete(self, request: LLMRequest) -> LLMResponse:
        """Execute chat completion against the provider."""
        raise NotImplementedError
