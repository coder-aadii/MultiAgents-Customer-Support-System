from __future__ import annotations

import hashlib
import math
import random

from app.providers.base import EmbeddingProvider


class FakeEmbeddingProvider(EmbeddingProvider):
    """Deterministic local fake embedding provider for testing and offline execution.

    Produces stable 1536-dimensional unit vectors derived from the SHA-256 hash of the input text.
    """

    dimensions: int = 1536

    async def embed_text(self, text: str) -> list[float]:
        return self._generate_vector(text)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self._generate_vector(t) for t in texts]

    def _generate_vector(self, text: str) -> list[float]:
        normalized = text.strip().lower()
        seed = int(hashlib.sha256(normalized.encode("utf-8")).hexdigest(), 16) % (2**32)
        rng = random.Random(seed)

        # Generate 1536 pseudo-random values with normal distribution
        raw_vals = [rng.gauss(0.0, 1.0) for _ in range(self.dimensions)]

        # Normalize to unit vector for cosine similarity
        norm = math.sqrt(sum(v * v for v in raw_vals))
        if norm == 0:
            return [0.0] * self.dimensions
        return [round(v / norm, 6) for v in raw_vals]

