from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from app.providers.base import (
    EmbeddingProvider,
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
from app.providers.circuit_breaker import CircuitBreaker

logger = logging.getLogger("mcss.providers.openai")


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """Production OpenAI-compatible embedding provider adapter."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str = "text-embedding-3-small",
        dimensions: int = 1536,
        timeout: float = 15.0,
        max_retries: int = 2,
        circuit_breaker: CircuitBreaker | None = None,
        metrics_hook: ProviderMetricsHook | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.dimensions = dimensions
        self.timeout = timeout
        self.max_retries = max(0, max_retries)
        self.circuit_breaker = circuit_breaker
        self.metrics_hook = metrics_hook or NoOpMetricsHook()

    async def embed_text(self, text: str) -> list[float]:
        res = await self.embed_batch([text])
        if not res:
            raise ProviderResponseError("Embedding provider returned empty response")
        return res[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        if self.circuit_breaker:
            return await self.circuit_breaker.execute(self._execute_batch, texts)
        return await self._execute_batch(texts)

    async def _execute_batch(self, texts: list[str]) -> list[list[float]]:
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

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            start_time = time.monotonic()
            self.metrics_hook.on_request_start("openai", "embeddings", self.model)
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.post(url, json=payload, headers=headers)
                    duration_ms = (time.monotonic() - start_time) * 1000

                    if resp.status_code == 200:
                        data = resp.json()
                        if "data" not in data or not isinstance(data["data"], list):
                            raise ProviderResponseError("Malformed response structure from embedding provider")

                        sorted_items = sorted(data["data"], key=lambda x: x.get("index", 0))
                        embeddings: list[list[float]] = []
                        for item in sorted_items:
                            emb = item.get("embedding")
                            if not isinstance(emb, list) or len(emb) != self.dimensions:
                                raise ProviderResponseError(
                                    f"Invalid embedding dimensions: expected {self.dimensions}, got {len(emb) if isinstance(emb, list) else 'invalid'}"
                                )
                            embeddings.append(emb)

                        self.metrics_hook.on_request_success(
                            "openai",
                            "embeddings",
                            self.model,
                            duration_ms,
                            {"total_tokens": data.get("usage", {}).get("total_tokens", 0)},
                        )
                        logger.info(
                            "OpenAI embedding batch succeeded: count=%d, duration_ms=%.2f",
                            len(texts),
                            duration_ms,
                        )
                        return embeddings

                    if resp.status_code in (401, 403):
                        raise ProviderAuthError(f"Embedding authentication failed with status {resp.status_code}")
                    if resp.status_code == 400:
                        raise ProviderBadRequestError(f"Embedding request rejected: status {resp.status_code}")
                    if resp.status_code == 429:
                        raise ProviderRateLimitError("Embedding rate limit exceeded")
                    if resp.status_code >= 500:
                        raise ProviderConnectionError(f"Embedding provider server error: status {resp.status_code}")

                    raise ProviderResponseError(f"Embedding provider unexpected status {resp.status_code}")

            except httpx.TimeoutException as e:
                duration_ms = (time.monotonic() - start_time) * 1000
                last_error = ProviderTimeoutError("Embedding request timed out")
                self.metrics_hook.on_request_failure("openai", "embeddings", self.model, duration_ms, last_error)
            except httpx.RequestError as e:
                duration_ms = (time.monotonic() - start_time) * 1000
                last_error = ProviderConnectionError(f"Embedding connection error: {type(e).__name__}")
                self.metrics_hook.on_request_failure("openai", "embeddings", self.model, duration_ms, last_error)
            except ProviderError as e:
                duration_ms = (time.monotonic() - start_time) * 1000
                last_error = e
                self.metrics_hook.on_request_failure("openai", "embeddings", self.model, duration_ms, last_error)
            except Exception as e:
                duration_ms = (time.monotonic() - start_time) * 1000
                last_error = ProviderResponseError(f"Unexpected error calling embeddings: {type(e).__name__}")
                self.metrics_hook.on_request_failure("openai", "embeddings", self.model, duration_ms, last_error)

            if attempt < self.max_retries and is_retryable_error(last_error):
                backoff = min(0.2 * (2**attempt), 1.0)
                logger.warning(
                    "Retrying transient embedding error (attempt %d/%d) after %.2fs",
                    attempt + 1,
                    self.max_retries,
                    backoff,
                )
                await asyncio.sleep(backoff)
            else:
                break

        assert last_error is not None
        raise last_error


class OpenAILLMProvider(LLMProvider):
    """Production OpenAI-compatible chat completion provider adapter.

    Reads configuration only. Never logs full prompts, secrets, or completions.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 15.0,
        max_retries: int = 2,
        circuit_breaker: CircuitBreaker | None = None,
        metrics_hook: ProviderMetricsHook | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_retries = max(0, max_retries)
        self.circuit_breaker = circuit_breaker
        self.metrics_hook = metrics_hook or NoOpMetricsHook()

    async def chat_complete(self, request: LLMRequest) -> LLMResponse:
        """Execute chat completion, guarded by circuit breaker if configured."""
        if self.circuit_breaker:
            return await self.circuit_breaker.execute(self._execute_chat, request)
        return await self._execute_chat(request)

    async def _execute_chat(self, request: LLMRequest) -> LLMResponse:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        effective_model = request.model or self.model
        payload: dict[str, Any] = {
            "model": effective_model,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }
        if request.response_format:
            payload["response_format"] = request.response_format

        effective_timeout = request.timeout or self.timeout
        last_error: Exception | None = None

        for attempt in range(self.max_retries + 1):
            start_time = time.monotonic()
            self.metrics_hook.on_request_start("openai", "chat_complete", effective_model)
            try:
                async with httpx.AsyncClient(timeout=effective_timeout) as client:
                    resp = await client.post(url, json=payload, headers=headers)
                    duration_ms = (time.monotonic() - start_time) * 1000

                    if resp.status_code == 200:
                        data = resp.json()
                        choices = data.get("choices")
                        if not choices or not isinstance(choices, list):
                            raise ProviderResponseError("Missing or invalid 'choices' in provider response")

                        msg = choices[0].get("message", {})
                        content = msg.get("content")
                        if content is None:
                            raise ProviderResponseError("Missing 'content' in provider message choice")

                        usage = data.get("usage", {})
                        prompt_tokens = usage.get("prompt_tokens", 0)
                        completion_tokens = usage.get("completion_tokens", 0)
                        total_tokens = usage.get("total_tokens", prompt_tokens + completion_tokens)

                        self.metrics_hook.on_request_success(
                            "openai",
                            "chat_complete",
                            self.model,
                            duration_ms,
                            {
                                "prompt_tokens": prompt_tokens,
                                "completion_tokens": completion_tokens,
                                "total_tokens": total_tokens,
                            },
                        )
                        logger.info(
                            "OpenAI chat completion succeeded: model=%s, duration_ms=%.2f, total_tokens=%d",
                            effective_model,
                            duration_ms,
                            total_tokens,
                        )
                        return LLMResponse(
                            content=content,
                            prompt_tokens=prompt_tokens,
                            completion_tokens=completion_tokens,
                            total_tokens=total_tokens,
                            model=data.get("model", effective_model),
                        )

                    if resp.status_code in (401, 403):
                        raise ProviderAuthError(f"LLM authentication failed with status {resp.status_code}: {resp.text}")
                    if resp.status_code == 400:
                        raise ProviderBadRequestError(f"LLM request rejected: status {resp.status_code}: {resp.text}")
                    if resp.status_code == 429 or (resp.status_code == 413 and "rate_limit_exceeded" in resp.text):
                        raise ProviderRateLimitError(f"LLM rate limit exceeded ({resp.status_code}): {resp.text}")
                    if resp.status_code >= 500:
                        raise ProviderConnectionError(f"LLM provider server error: status {resp.status_code}: {resp.text}")

                    raise ProviderResponseError(f"LLM provider unexpected status {resp.status_code}: {resp.text}")

            except httpx.TimeoutException as e:
                duration_ms = (time.monotonic() - start_time) * 1000
                last_error = ProviderTimeoutError("LLM chat request timed out")
                self.metrics_hook.on_request_failure("openai", "chat_complete", effective_model, duration_ms, last_error)
            except httpx.RequestError as e:
                duration_ms = (time.monotonic() - start_time) * 1000
                last_error = ProviderConnectionError(f"LLM connection error: {type(e).__name__}")
                self.metrics_hook.on_request_failure("openai", "chat_complete", effective_model, duration_ms, last_error)
            except ProviderError as e:
                duration_ms = (time.monotonic() - start_time) * 1000
                last_error = e
                self.metrics_hook.on_request_failure("openai", "chat_complete", effective_model, duration_ms, last_error)
            except Exception as e:
                duration_ms = (time.monotonic() - start_time) * 1000
                last_error = ProviderResponseError(f"Unexpected error calling chat completion: {type(e).__name__}")
                self.metrics_hook.on_request_failure("openai", "chat_complete", effective_model, duration_ms, last_error)

            if attempt < self.max_retries and is_retryable_error(last_error):
                if isinstance(last_error, ProviderRateLimitError):
                    backoff = min(1.0 * (2**attempt), 3.0)
                else:
                    backoff = min(0.2 * (2**attempt), 1.0)
                logger.warning(
                    "Retrying transient LLM error (attempt %d/%d) after %.2fs",
                    attempt + 1,
                    self.max_retries,
                    backoff,
                )
                await asyncio.sleep(backoff)
            else:
                break

        assert last_error is not None
        raise last_error
