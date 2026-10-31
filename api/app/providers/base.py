from __future__ import annotations

from abc import ABC, abstractmethod


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

