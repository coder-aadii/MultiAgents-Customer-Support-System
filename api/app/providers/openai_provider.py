from __future__ import annotations

from typing import Any

import httpx

from app.providers.base import EmbeddingProvider


class ProviderError(Exception):
    """Safe wrapper for external LLM or embedding provider failures."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """Production OpenAI-compatible embedding provider adapter."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str = "text-embedding-3-small",
        dimensions: int = 1536,
        timeout: float = 15.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.dimensions = dimensions
        self.timeout = timeout

    async def embed_text(self, text: str) -> list[float]:
        res = await self.embed_batch([text])
        if not res:
            raise ProviderError("Embedding provider returned empty response")
        return res[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        url = f"{self.base_url}/embeddings"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "input": texts,
            "model": self.model,
            "dimensions": self.dimensions,
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(url, json=payload, headers=headers)
                if resp.status_code != 200:
                    raise ProviderError(
                        f"Embedding provider returned status code {resp.status_code}"
                    )
                data = resp.json()
        except httpx.TimeoutException as e:
            raise ProviderError("Embedding provider request timed out") from e
        except httpx.HTTPError as e:
            raise ProviderError(f"Embedding provider HTTP connection error: {e}") from e
        except Exception as e:
            if isinstance(e, ProviderError):
                raise
            raise ProviderError(f"Unexpected error calling embedding provider: {e}") from e

        if "data" not in data or not isinstance(data["data"], list):
            raise ProviderError("Malformed response structure from embedding provider")

        sorted_items = sorted(data["data"], key=lambda x: x.get("index", 0))
        embeddings: list[list[float]] = []

        for item in sorted_items:
            emb = item.get("embedding")
            if not isinstance(emb, list) or len(emb) != self.dimensions:
                raise ProviderError(
                    f"Invalid embedding dimensions from provider: expected {self.dimensions}, got {len(emb) if isinstance(emb, list) else 'invalid'}"
                )
            embeddings.append(emb)

        return embeddings

