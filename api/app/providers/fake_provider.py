from __future__ import annotations

import asyncio
import hashlib
import json
import math
import random
import re
from typing import Any, Callable

from app.providers.base import (
    EmbeddingProvider,
    LLMProvider,
    LLMRequest,
    LLMResponse,
)
from app.providers.circuit_breaker import CircuitBreaker


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


class FakeLLMProvider(LLMProvider):
    """Deterministic, fixture-controlled fake LLM provider for tests and local execution.

    Zero external network calls. Supports canned responses, error simulation,
    call history inspection, and circuit breaker wrapping.
    """

    UUID_REGEX = re.compile(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        re.IGNORECASE,
    )

    def __init__(
        self,
        circuit_breaker: CircuitBreaker | None = None,
        default_model: str = "fake-model",
    ) -> None:
        self.circuit_breaker = circuit_breaker
        self.default_model = default_model
        self._response_queue: list[LLMResponse | str] = []
        self._error_queue: list[Exception] = []
        self._custom_handler: Callable[[LLMRequest], LLMResponse] | None = None
        self._calls: list[LLMRequest] = []
        self.delay_seconds: float = 0.0

    @property
    def calls(self) -> list[LLMRequest]:
        """Inspection of recorded requests for test assertions."""
        return list(self._calls)

    @property
    def call_count(self) -> int:
        return len(self._calls)

    def queue_response(self, response: LLMResponse | str | dict[str, Any]) -> None:
        """Queue a fixture response to be returned on subsequent invocation."""
        if isinstance(response, dict):
            self._response_queue.append(json.dumps(response))
        else:
            self._response_queue.append(response)

    def queue_error(self, error: Exception) -> None:
        """Queue an exception to be simulated on subsequent invocation."""
        self._error_queue.append(error)

    def set_custom_handler(self, handler: Callable[[LLMRequest], LLMResponse] | None) -> None:
        """Set a dynamic handler function to determine output based on request."""
        self._custom_handler = handler

    def reset(self) -> None:
        """Clear recorded calls, response queues, and error queues."""
        self._response_queue.clear()
        self._error_queue.clear()
        self._calls.clear()
        self._custom_handler = None
        self.delay_seconds = 0.0

    async def chat_complete(self, request: LLMRequest) -> LLMResponse:
        """Execute chat completion, optionally wrapped in circuit breaker."""
        if self.circuit_breaker:
            return await self.circuit_breaker.execute(self._execute_internal, request)
        return await self._execute_internal(request)

    async def _execute_internal(self, request: LLMRequest) -> LLMResponse:
        self._calls.append(request)

        if self.delay_seconds > 0:
            await asyncio.sleep(self.delay_seconds)

        # 1. Pop simulated error if queued
        if self._error_queue:
            err = self._error_queue.pop(0)
            raise err

        # 2. Pop canned response if queued
        if self._response_queue:
            item = self._response_queue.pop(0)
            if isinstance(item, LLMResponse):
                return item
            return LLMResponse(
                content=item,
                prompt_tokens=30,
                completion_tokens=len(item) // 4,
                total_tokens=30 + (len(item) // 4),
                model=self.default_model,
            )

        # 3. Custom handler if registered
        if self._custom_handler:
            return self._custom_handler(request)

        # 4. Fallback deterministic generator
        return self._generate_deterministic_response(request)

    def _generate_deterministic_response(self, request: LLMRequest) -> LLMResponse:
        full_text = " ".join([m.content for m in request.messages])
        system_text = " ".join([m.content for m in request.messages if m.role == "system"])
        user_messages = [m.content for m in request.messages if m.role == "user"]
        user_text = " ".join(user_messages)
        current_user_text = user_messages[-1] if user_messages else user_text

        # Check if classifier prompt
        if "classify" in system_text.lower():
            content = self._deterministic_classifier(current_user_text)
        elif "greeting" in system_text.lower():
            content = self._deterministic_greeting_generator(system_text, user_text)
        elif "unauthenticated" in system_text.lower() or "log in to their account first" in system_text.lower():
            content = self._deterministic_auth_required_generator(system_text, user_text)
        elif "sources" in system_text.lower() or "cited_chunk_ids" in system_text.lower():
            content = self._deterministic_answer_generator(system_text, user_text)
        elif "supervisor" in system_text.lower() or "investigation plan" in system_text.lower():
            lower_msg = current_user_text.lower()
            if any(w in lower_msg for w in ("cancel", "stop charging", "close account")):
                content = json.dumps({"outcome": "handoff", "reason": "account_or_billing_request"})
            elif "workspace" in lower_msg and ("subscription" in lower_msg or "sub-" in lower_msg):
                content = json.dumps({
                    "user_intents": ["inquiry"],
                    "tasks": [
                        {
                            "task_id": "investigate_workspace",
                            "assigned_agent": "universal_entity_specialist",
                            "task_description": "Investigate workspace status.",
                            "required_inputs": {"entity_type": "workspace"},
                            "dependencies": [],
                        },
                        {
                            "task_id": "investigate_subscription",
                            "assigned_agent": "universal_entity_specialist",
                            "task_description": "Investigate subscription status.",
                            "required_inputs": {"entity_type": "subscription"},
                            "dependencies": [],
                        },
                    ],
                })
            elif any(w in lower_msg for w in (
                "workspace", "ws-", "sub-", "quota-", "api_quota",
                "bank account", "checking account", "acc-", "transaction", "txn-", "wire", "wire-", "wire_transfer",
                "patient", "pat-", "appointment", "apt-", "prescription", "rx-",
            )):
                content = json.dumps({
                    "user_intents": ["inquiry"],
                    "tasks": [{
                        "task_id": "primary_investigation",
                        "assigned_agent": "universal_entity_specialist",
                        "task_description": "Investigate multi-industry enterprise entity.",
                        "required_inputs": {"query": current_user_text},
                        "dependencies": [],
                    }],
                })
            else:
                content = json.dumps({
                    "user_intents": ["inquiry"],
                    "tasks": [{
                        "task_id": "primary_investigation",
                        "assigned_agent": "knowledge_specialist",
                        "task_description": "Investigate inquiry using knowledge base.",
                        "required_inputs": {"query": current_user_text},
                        "dependencies": [],
                    }],
                })
        elif "reflection" in system_text.lower() or "critic" in system_text.lower():
            content = json.dumps({
                "verdict": "approve",
                "grounded": True,
                "complete": True,
                "issues": [],
                "recommended_next_step": "deliver",
                "missing_information": [],
            })
        elif request.response_format and request.response_format.get("type") == "json_object":
            content = json.dumps({"status": "success", "message": "Deterministic response"})
        else:
            # Generic deterministic string
            h = hashlib.sha256(full_text.encode("utf-8")).hexdigest()[:8]
            content = f"Deterministic response {h}"

        prompt_tokens = max(1, len(full_text) // 4)
        completion_tokens = max(1, len(content) // 4)

        return LLMResponse(
            content=content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            model=self.default_model,
        )

    def _deterministic_classifier(self, user_text: str) -> str:
        text_lower = user_text.lower()
        if any(w in text_lower for w in ["human", "agent", "representative", "person", "operator", "support team", "staff"]):
            return json.dumps({"action": "handoff", "reason": "human_request"})
        if any(w in text_lower for w in ["legal", "lawyer", "medical", "doctor", "emergency", "security", "exploit", "vulnerability", "hack"]):
            return json.dumps({"action": "handoff", "reason": "unsafe_domain"})
        if any(w in text_lower for w in ["world cup", "python script", "simulate", "soccer", "orbit", "weather", "recipe"]):
            return json.dumps({"action": "handoff", "reason": "unsupported_request"})
        if any(w in text_lower for w in ["what is the date", "date today", "what is the time", "tell me a joke", "who is the president"]):
            return json.dumps({"action": "out_of_scope", "reason": "out_of_scope"})

        # Generic how-to / process questions, FAQ, definitions -> answer
        is_process_query = any(
            p in text_lower
            for p in [
                "how can i",
                "how do i",
                "how to",
                "how does",
                "what does",
                "what is",
                "meaning of",
                "explain",
                "documentation",
                "policy",
                "procedure",
            ]
        )
        is_specific_record = any(
            p in text_lower
            for p in [
                "show my",
                "list my",
                "my recent",
                "recent orders",
                "when was my order",
                "when was order",
                "status of my order",
                "status of my",
                "is my order",
                "track my",
            ]
        ) or bool(re.search(r"\b(?:ord-|order\s+[0-9a-f]{6,})\b", text_lower))

        if is_process_query and not is_specific_record:
            return json.dumps({"action": "answer", "reason": "faq_match"})

        if is_specific_record:
            return json.dumps({"action": "business_data", "reason": "customer_order_lookup"})

        if any(
            w in text_lower
            for w in [
                "refund",
                "cancel",
                "payment",
                "order",
                "billing",
                "account",
                "name",
                "email",
                "who am i",
                "profile",
            ]
        ):
            return json.dumps({"action": "handoff", "reason": "account_or_billing_request"})
        if "clarify" in text_lower or "unclear" in text_lower:
            return json.dumps({"action": "clarify", "reason": "missing_detail"})
        return json.dumps({"action": "answer", "reason": "faq_match"})

    def _deterministic_answer_generator(self, system_text: str, user_text: str) -> str:
        # Extract UUID chunk IDs if present in system text (sources)
        uuids = self.UUID_REGEX.findall(system_text)
        if uuids:
            return json.dumps({
                "action": "answer",
                "answer": f"According to verified documentation: {user_text.strip()[:60]}.",
                "cited_chunk_ids": [uuids[0]],
                "confidence": 0.95,
            })
        return json.dumps({
            "action": "handoff",
            "reason": "no_evidence_available",
        })

    def _deterministic_greeting_generator(self, system_text: str, user_text: str) -> str:
        sys_lower = system_text.lower()
        if "apex" in sys_lower or "bank" in sys_lower:
            return "Hello! Welcome to Apex Global Bank. How can I help you today? You can check your account balances, track wire transfers, or dispute transactions."
        if "cloudpulse" in sys_lower or "workspace" in sys_lower:
            return "Hello! Welcome to CloudPulse SaaS. How can I help you today? You can check your cloud workspaces, subscription tiers, or API quotas."
        if "medicare" in sys_lower or "telehealth" in sys_lower or "appointment" in sys_lower:
            return "Hello! Welcome to MediCare Telehealth. How can I help you today? You can check upcoming appointments, refill active prescriptions, or request nurse triage."
        return "Hello! How can I help you today? You can ask about our return and refund policies, track a shipment, or look up your orders."

    def _deterministic_auth_required_generator(self, system_text: str, user_text: str) -> str:
        sys_lower = system_text.lower()
        if "apex" in sys_lower or "bank" in sys_lower:
            return (
                "To view, track, or manage your bank accounts and financial records, please log in to your account first. "
                "You can sign in using the **1-Click Customer Switcher** or the login form on this page to securely access your banking details."
            )
        if "cloudpulse" in sys_lower or "workspace" in sys_lower:
            return (
                "To view, track, or manage your cloud workspaces and subscriptions, please log in to your account first. "
                "You can sign in using the **1-Click Customer Switcher** or the login form on this page to securely access your cloud telemetry."
            )
        if "medicare" in sys_lower or "telehealth" in sys_lower or "appointment" in sys_lower:
            return (
                "To view, track, or manage your appointments and prescription records, please log in to your account first. "
                "You can sign in using the **1-Click Customer Switcher** or the login form on this page to securely access your patient portal."
            )
        return (
            "To view, track, or manage your orders and account details, please log in to your account first. "
            "You can sign in using the **1-Click Customer Switcher** or the login form on this page to securely view your orders. "
            "If you have general questions about our return policy or shipping procedures, feel free to ask!"
        )


