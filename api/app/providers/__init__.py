from __future__ import annotations

import os

from app.config import settings
from app.providers.base import (
    EmbeddingProvider,
    LLMMessage,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    NoOpMetricsHook,
    ProviderAuthError,
    ProviderBadRequestError,
    ProviderCircuitOpenError,
    ProviderConnectionError,
    ProviderError,
    ProviderMetricsHook,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    is_retryable_error,
)
from app.providers.circuit_breaker import CircuitBreaker, CircuitState
from app.providers.fake_provider import FakeEmbeddingProvider, FakeLLMProvider
from app.providers.openai_provider import OpenAIEmbeddingProvider, OpenAILLMProvider

__all__ = [
    "CircuitBreaker",
    "CircuitState",
    "EmbeddingProvider",
    "FakeEmbeddingProvider",
    "FakeLLMProvider",
    "LLMMessage",
    "LLMProvider",
    "LLMRequest",
    "LLMResponse",
    "NoOpMetricsHook",
    "OpenAIEmbeddingProvider",
    "OpenAILLMProvider",
    "ProviderAuthError",
    "ProviderBadRequestError",
    "ProviderCircuitOpenError",
    "ProviderConnectionError",
    "ProviderError",
    "ProviderMetricsHook",
    "ProviderRateLimitError",
    "ProviderResponseError",
    "ProviderTimeoutError",
    "get_embedding_circuit_breaker",
    "get_embedding_provider",
    "get_llm_circuit_breaker",
    "get_llm_provider",
    "is_retryable_error",
]

# Shared circuit breakers across process lifetime
_llm_circuit_breaker = CircuitBreaker(name="llm_provider", failure_threshold=3, recovery_timeout=30.0)
_embedding_circuit_breaker = CircuitBreaker(name="embedding_provider", failure_threshold=3, recovery_timeout=30.0)


def get_llm_circuit_breaker() -> CircuitBreaker:
    return _llm_circuit_breaker


def get_embedding_circuit_breaker() -> CircuitBreaker:
    return _embedding_circuit_breaker


def get_embedding_provider() -> EmbeddingProvider:
    """Factory to retrieve configured embedding provider instance."""
    if (
        settings.is_test_or_fake
        or os.environ.get("MCSS_ENV") == "test"
        or settings.EMBEDDING_BASE_URL in ("fake", "")
        or not settings.EMBEDDING_API_KEY
        or settings.EMBEDDING_MODEL in ("", "replace-me", "fake")
    ):
        return FakeEmbeddingProvider()

    return OpenAIEmbeddingProvider(
        base_url=settings.EMBEDDING_BASE_URL,
        api_key=settings.EMBEDDING_API_KEY,
        model=settings.EMBEDDING_MODEL or "text-embedding-3-small",
        dimensions=settings.EMBEDDING_DIMENSIONS,
        circuit_breaker=_embedding_circuit_breaker,
    )


def get_llm_provider() -> LLMProvider:
    """Factory to retrieve configured LLM provider instance."""
    if (
        settings.is_test_or_fake
        or os.environ.get("MCSS_ENV") == "test"
        or os.environ.get("MCSS_LLM_BASE_URL") == "fake"
        or not settings.LLM_API_KEY
        or settings.LLM_BASE_URL in ("fake", "")
        or settings.LLM_CHAT_MODEL in ("", "replace-me", "fake")
    ):
        return FakeLLMProvider(circuit_breaker=_llm_circuit_breaker)

    return OpenAILLMProvider(
        base_url=settings.LLM_BASE_URL,
        api_key=settings.LLM_API_KEY,
        model=settings.LLM_CHAT_MODEL,
        circuit_breaker=_llm_circuit_breaker,
    )
