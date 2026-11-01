"""Tests for Phase 4 Sub-phase 4.1: Provider boundary, Circuit Breaker, and Redaction."""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.common.redaction import (
    REDACTED_PLACEHOLDER,
    is_sensitive_key,
    redact_llm_payload_for_logging,
    redact_sensitive_data,
)
from app.providers import (
    CircuitBreaker,
    CircuitState,
    FakeEmbeddingProvider,
    FakeLLMProvider,
    LLMMessage,
    LLMRequest,
    LLMResponse,
    OpenAILLMProvider,
    ProviderAuthError,
    ProviderBadRequestError,
    ProviderCircuitOpenError,
    ProviderConnectionError,
    ProviderError,
    ProviderMetricsHook,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    get_embedding_provider,
    get_llm_provider,
    is_retryable_error,
)


class RecordingMetricsHook(ProviderMetricsHook):
    """Test hook that records telemetry invocations."""

    def __init__(self) -> None:
        self.starts: list[tuple[str, str, str]] = []
        self.successes: list[tuple[str, str, str, float, dict[str, int]]] = []
        self.failures: list[tuple[str, str, str, float, Exception]] = []

    def on_request_start(self, provider: str, operation: str, model: str) -> None:
        self.starts.append((provider, operation, model))

    def on_request_success(
        self,
        provider: str,
        operation: str,
        model: str,
        duration_ms: float,
        tokens: dict[str, int],
    ) -> None:
        self.successes.append((provider, operation, model, duration_ms, tokens))

    def on_request_failure(
        self,
        provider: str,
        operation: str,
        model: str,
        duration_ms: float,
        error: Exception,
    ) -> None:
        self.failures.append((provider, operation, model, duration_ms, error))


# --- Fake Provider Determinism Tests ---


@pytest.mark.asyncio
async def test_fake_embedding_provider_determinism():
    """Ensure FakeEmbeddingProvider generates deterministic, unit-normalized embeddings."""
    provider = FakeEmbeddingProvider()

    vec1 = await provider.embed_text("How do I reset my password?")
    vec2 = await provider.embed_text("How do I reset my password?")
    vec3 = await provider.embed_text("Different query entirely")

    assert len(vec1) == 1536
    assert vec1 == vec2
    assert vec1 != vec3

    # Check approximate unit length
    magnitude = sum(x * x for x in vec1) ** 0.5
    assert pytest.approx(magnitude, 0.01) == 1.0


@pytest.mark.asyncio
async def test_fake_llm_provider_determinism():
    """Ensure FakeLLMProvider generates deterministic outputs for identical requests."""
    provider = FakeLLMProvider()

    req = LLMRequest(
        messages=[
            LLMMessage(role="system", content="You are a support assistant."),
            LLMMessage(role="user", content="Hello, can you help me?"),
        ]
    )

    res1 = await provider.chat_complete(req)
    res2 = await provider.chat_complete(req)

    assert res1.content == res2.content
    assert res1.prompt_tokens == res2.prompt_tokens
    assert res1.completion_tokens == res2.completion_tokens
    assert provider.call_count == 2


@pytest.mark.asyncio
async def test_fake_llm_provider_fixture_queues():
    """Ensure FakeLLMProvider respects queued responses, errors, and custom handlers."""
    provider = FakeLLMProvider()

    # 1. Queue canned response
    provider.queue_response({"action": "answer", "answer": "Canned answer"})
    req = LLMRequest(messages=[LLMMessage(role="user", content="Test")])
    res = await provider.chat_complete(req)
    data = json.loads(res.content)
    assert data["action"] == "answer"
    assert data["answer"] == "Canned answer"

    # 2. Queue simulated error
    provider.queue_error(ProviderTimeoutError("Simulated timeout"))
    with pytest.raises(ProviderTimeoutError) as exc_info:
        await provider.chat_complete(req)
    assert "Simulated timeout" in str(exc_info.value)

    # 3. Custom handler
    provider.set_custom_handler(
        lambda r: LLMResponse(content="Custom handler response", model="custom-model")
    )
    res = await provider.chat_complete(req)
    assert res.content == "Custom handler response"
    assert res.model == "custom-model"

    # 4. Reset
    provider.reset()
    assert provider.call_count == 0
    res = await provider.chat_complete(req)
    assert "Deterministic response" in res.content


@pytest.mark.asyncio
async def test_fake_llm_provider_deterministic_heuristics():
    """Verify built-in deterministic routing for classifier and answer prompts."""
    provider = FakeLLMProvider()

    # Classifier human request
    c_req = LLMRequest(
        messages=[
            LLMMessage(role="system", content="You classify one customer-support message. Actions: answer, clarify, handoff."),
            LLMMessage(role="user", content="I want to speak with a human agent please"),
        ]
    )
    c_res = await provider.chat_complete(c_req)
    assert json.loads(c_res.content) == {"action": "handoff", "reason": "human_request"}

    # Classifier refund request
    r_req = LLMRequest(
        messages=[
            LLMMessage(role="system", content="You classify one customer-support message."),
            LLMMessage(role="user", content="I need a refund for my last order"),
        ]
    )
    r_res = await provider.chat_complete(r_req)
    assert json.loads(r_res.content) == {"action": "handoff", "reason": "account_or_billing_request"}

    # Classifier standard query
    s_req = LLMRequest(
        messages=[
            LLMMessage(role="system", content="You classify one customer-support message."),
            LLMMessage(role="user", content="Where can I find your documentation?"),
        ]
    )
    s_res = await provider.chat_complete(s_req)
    assert json.loads(s_res.content) == {"action": "answer", "reason": "faq_match"}

    # Answer generator with UUID in sources
    uuid_str = "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"
    a_req = LLMRequest(
        messages=[
            LLMMessage(role="system", content=f"Answer only from SOURCES.\n[Source {uuid_str}]\nMCSS is open source."),
            LLMMessage(role="user", content="What is MCSS?"),
        ]
    )
    a_res = await provider.chat_complete(a_req)
    a_data = json.loads(a_res.content)
    assert a_data["action"] == "answer"
    assert a_data["cited_chunk_ids"] == [uuid_str]
    assert a_data["confidence"] == 0.95


# --- Circuit Breaker Tests ---


@pytest.mark.asyncio
async def test_circuit_breaker_transitions_closed_to_open():
    """Ensure circuit breaker opens after consecutive failures reach threshold and suppresses calls."""
    breaker = CircuitBreaker(name="test_cb", failure_threshold=3, recovery_timeout=0.2)
    assert breaker.state == CircuitState.CLOSED
    assert breaker.is_available()

    async def fail_call():
        raise ProviderConnectionError("Service unavailable")

    # Failures 1 and 2 keep breaker CLOSED
    for _ in range(2):
        with pytest.raises(ProviderConnectionError):
            await breaker.execute(fail_call)
        assert breaker.state == CircuitState.CLOSED

    # Failure 3 trips breaker to OPEN
    with pytest.raises(ProviderConnectionError):
        await breaker.execute(fail_call)
    assert breaker.state == CircuitState.OPEN
    assert not breaker.is_available()

    # Subsequent call fails immediately with ProviderCircuitOpenError without invoking target
    target_mock = AsyncMock()
    with pytest.raises(ProviderCircuitOpenError) as exc_info:
        await breaker.execute(target_mock)
    assert "is OPEN" in str(exc_info.value)
    target_mock.assert_not_called()


@pytest.mark.asyncio
async def test_circuit_breaker_half_open_probe_success():
    """Ensure breaker evaluates to HALF_OPEN after recovery timeout and closes on successful probe."""
    breaker = CircuitBreaker(name="test_cb", failure_threshold=2, recovery_timeout=0.1)

    async def fail_call():
        raise ProviderConnectionError("Error")

    # Trip breaker to OPEN
    for _ in range(2):
        with pytest.raises(ProviderConnectionError):
            await breaker.execute(fail_call)
    assert breaker.state == CircuitState.OPEN

    # Wait for recovery timeout
    await asyncio.sleep(0.12)
    assert breaker.state == CircuitState.HALF_OPEN

    # Probe call succeeds
    async def success_call():
        return "success"

    result = await breaker.execute(success_call)
    assert result == "success"
    assert breaker.state == CircuitState.CLOSED
    assert breaker.consecutive_failures == 0


@pytest.mark.asyncio
async def test_circuit_breaker_half_open_probe_failure_reopens():
    """Ensure breaker reopens immediately if probe in HALF_OPEN fails."""
    breaker = CircuitBreaker(name="test_cb", failure_threshold=2, recovery_timeout=0.1)

    async def fail_call():
        raise ProviderConnectionError("Error")

    # Trip breaker to OPEN
    for _ in range(2):
        with pytest.raises(ProviderConnectionError):
            await breaker.execute(fail_call)
    assert breaker.state == CircuitState.OPEN

    # Wait for recovery timeout
    await asyncio.sleep(0.12)
    assert breaker.state == CircuitState.HALF_OPEN

    # Probe fails
    with pytest.raises(ProviderConnectionError):
        await breaker.execute(fail_call)

    # Immediately back to OPEN
    assert breaker.state == CircuitState.OPEN


@pytest.mark.asyncio
async def test_circuit_breaker_manual_controls():
    """Verify manual trip and reset capabilities."""
    breaker = CircuitBreaker(name="test_cb", failure_threshold=3)

    breaker.trip()
    assert breaker.state == CircuitState.OPEN

    breaker.reset()
    assert breaker.state == CircuitState.CLOSED
    assert breaker.consecutive_failures == 0


@pytest.mark.asyncio
async def test_circuit_breaker_does_not_trip_on_non_retryable_client_error():
    """Invariant: Non-retryable client errors (e.g. 400 Bad Request) do not trip the circuit breaker."""
    breaker = CircuitBreaker(name="test_cb", failure_threshold=2, recovery_timeout=0.1)

    async def client_error_call():
        raise ProviderBadRequestError("Invalid JSON input")

    for _ in range(5):
        with pytest.raises(ProviderBadRequestError):
            await breaker.execute(client_error_call)

    # State must remain CLOSED because errors were non-retryable client errors
    assert breaker.state == CircuitState.CLOSED
    assert breaker.consecutive_failures == 0


@pytest.mark.asyncio
async def test_circuit_breaker_single_probe_in_half_open():
    """Invariant: In HALF_OPEN, concurrent calls are suppressed while the single probe is active."""
    breaker = CircuitBreaker(name="test_cb", failure_threshold=1, recovery_timeout=0.05)

    async def fail_call():
        raise ProviderConnectionError("503 Service Unavailable")

    with pytest.raises(ProviderConnectionError):
        await breaker.execute(fail_call)
    assert breaker.state == CircuitState.OPEN

    # Wait for recovery timeout to transition to HALF_OPEN
    await asyncio.sleep(0.06)
    assert breaker.state == CircuitState.HALF_OPEN

    probe_started = asyncio.Event()

    async def slow_probe_call():
        probe_started.set()
        await asyncio.sleep(0.1)
        return "success"

    async def concurrent_call():
        await probe_started.wait()
        return await breaker.execute(lambda: asyncio.sleep(0.01))

    probe_task = asyncio.create_task(breaker.execute(slow_probe_call))

    with pytest.raises(ProviderCircuitOpenError) as exc_info:
        await concurrent_call()

    assert "probing" in str(exc_info.value)

    res = await probe_task
    assert res == "success"
    assert breaker.state == CircuitState.CLOSED



# --- OpenAI Provider & Retry Classification Tests ---


@pytest.mark.asyncio
async def test_error_retry_classification():
    """Verify transient vs non-transient error classification."""
    assert is_retryable_error(ProviderTimeoutError())
    assert is_retryable_error(ProviderConnectionError())
    assert is_retryable_error(ProviderRateLimitError())
    assert is_retryable_error(TimeoutError())

    assert not is_retryable_error(ProviderAuthError())
    assert not is_retryable_error(ProviderBadRequestError())
    assert not is_retryable_error(ProviderResponseError())
    assert not is_retryable_error(ProviderCircuitOpenError())
    assert not is_retryable_error(ValueError("Invalid syntax"))


@pytest.mark.asyncio
async def test_openai_llm_provider_transient_retry():
    """Verify OpenAI adapter retries transient 503 and succeeds on next attempt."""
    hook = RecordingMetricsHook()
    provider = OpenAILLMProvider(
        base_url="https://api.example.com/v1",
        api_key="test-key",
        model="gpt-4o-mini",
        max_retries=2,
        metrics_hook=hook,
    )

    req = LLMRequest(messages=[LLMMessage(role="user", content="Hello")])

    mock_resp_503 = httpx.Response(status_code=503, content=b"Service Unavailable")
    mock_resp_200 = httpx.Response(
        status_code=200,
        json={
            "choices": [{"message": {"role": "assistant", "content": "Success response"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "model": "gpt-4o-mini",
        },
    )

    with patch.object(httpx.AsyncClient, "post", side_effect=[mock_resp_503, mock_resp_200]) as mock_post:
        res = await provider.chat_complete(req)

        assert res.content == "Success response"
        assert res.total_tokens == 15
        assert mock_post.call_count == 2

    # Check metrics
    assert len(hook.starts) == 2
    assert len(hook.failures) == 1
    assert len(hook.successes) == 1


@pytest.mark.asyncio
async def test_openai_llm_provider_non_retryable_auth_fails_fast():
    """Verify 401 Authentication failure raises immediately without retry."""
    hook = RecordingMetricsHook()
    provider = OpenAILLMProvider(
        base_url="https://api.example.com/v1",
        api_key="bad-key",
        model="gpt-4o-mini",
        max_retries=2,
        metrics_hook=hook,
    )

    req = LLMRequest(messages=[LLMMessage(role="user", content="Hello")])
    mock_resp_401 = httpx.Response(status_code=401, content=b"Unauthorized")

    with patch.object(httpx.AsyncClient, "post", return_value=mock_resp_401) as mock_post:
        with pytest.raises(ProviderAuthError):
            await provider.chat_complete(req)

        # Fails immediately: no retry
        assert mock_post.call_count == 1

    assert len(hook.failures) == 1
    assert len(hook.successes) == 0


@pytest.mark.asyncio
async def test_openai_llm_provider_schema_response_errors():
    """Verify malformed JSON responses from provider raise ProviderResponseError."""
    provider = OpenAILLMProvider(
        base_url="https://api.example.com/v1",
        api_key="test-key",
        model="gpt-4o-mini",
        max_retries=0,
    )

    req = LLMRequest(messages=[LLMMessage(role="user", content="Hello")])

    # Missing choices
    with patch.object(httpx.AsyncClient, "post", return_value=httpx.Response(status_code=200, json={})):
        with pytest.raises(ProviderResponseError) as exc_info:
            await provider.chat_complete(req)
        assert "choices" in str(exc_info.value)

    # Missing content inside choice
    with patch.object(
        httpx.AsyncClient,
        "post",
        return_value=httpx.Response(status_code=200, json={"choices": [{"message": {}}]}),
    ):
        with pytest.raises(ProviderResponseError) as exc_info:
            await provider.chat_complete(req)
        assert "content" in str(exc_info.value)


# --- Redaction & Privacy Invariants ---


def test_redaction_patterns_and_helpers():
    """Verify redaction strips prompts, completions, tokens, and credentials from dictionaries and logs."""
    assert is_sensitive_key("prompt")
    assert is_sensitive_key("system_prompt")
    assert is_sensitive_key("raw_completion")
    assert is_sensitive_key("api_key")
    assert is_sensitive_key("visitor_message")

    data = {
        "user_id": "123",
        "api_key": "sk-secret123",
        "system_prompt": "You are a secret bot",
        "visitor_message": "My credit card is 1234",
        "nested": {"token": "secret_jwt", "safe_field": 42},
    }

    redacted = redact_sensitive_data(data)
    assert redacted["api_key"] == REDACTED_PLACEHOLDER
    assert redacted["system_prompt"] == REDACTED_PLACEHOLDER
    assert redacted["visitor_message"] == REDACTED_PLACEHOLDER
    assert redacted["nested"]["token"] == REDACTED_PLACEHOLDER
    assert redacted["nested"]["safe_field"] == 42
    assert redacted["user_id"] == "123"

    # LLM payload logger
    payload = {
        "model": "gpt-4o-mini",
        "temperature": 0.0,
        "messages": [
            {"role": "system", "content": "hidden system instructions"},
            {"role": "user", "content": "visitor query"},
        ],
        "completion": "AI answer text",
    }
    safe_log = redact_llm_payload_for_logging(payload)
    assert safe_log["model"] == "gpt-4o-mini"
    assert safe_log["message_count"] == 2
    assert safe_log["completion"] == REDACTED_PLACEHOLDER
    assert "messages" not in safe_log or not isinstance(safe_log.get("messages"), list)


def test_provider_factories_adhere_to_isolation():
    """Ensure get_llm_provider and get_embedding_provider default to deterministic fakes in test environment."""
    llm = get_llm_provider()
    embedding = get_embedding_provider()

    assert isinstance(llm, FakeLLMProvider)
    assert isinstance(embedding, FakeEmbeddingProvider)

