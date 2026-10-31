from __future__ import annotations

import os

from app.config import settings
from app.providers.base import EmbeddingProvider
from app.providers.fake_provider import FakeEmbeddingProvider
from app.providers.openai_provider import OpenAIEmbeddingProvider, ProviderError

__all__ = [
    "EmbeddingProvider",
    "FakeEmbeddingProvider",
    "OpenAIEmbeddingProvider",
    "ProviderError",
    "get_embedding_provider",
]


def get_embedding_provider() -> EmbeddingProvider:
    """Factory to retrieve configured embedding provider instance."""
    # Deterministic fake provider when configured for test, development without keys, or fake
    if (
        settings.is_test_or_fake
        or os.environ.get("MCSS_ENV") == "test"
        or os.environ.get("MCSS_LLM_BASE_URL") == "fake"
        or not settings.LLM_API_KEY
        or settings.LLM_BASE_URL in ("fake", "http://localhost:3001/v1")
        or settings.EMBEDDING_MODEL in ("", "replace-me")
    ):
        return FakeEmbeddingProvider()

    return OpenAIEmbeddingProvider(
        base_url=settings.LLM_BASE_URL,
        api_key=settings.LLM_API_KEY,
        model=settings.EMBEDDING_MODEL or "text-embedding-3-small",
        dimensions=settings.EMBEDDING_DIMENSIONS,
    )

