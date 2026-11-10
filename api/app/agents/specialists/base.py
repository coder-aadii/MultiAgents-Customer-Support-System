"""Shared non-executing helpers for scope-fenced specialist agents."""

from __future__ import annotations

from typing import Any

from app.agents.base import AgentContext, AgentResult, BaseAgent
from app.agents.schemas import AgentExecutionStatus
from app.agents.specialists.schemas import SpecialistFindings, SpecialistStatus


class SpecialistAgent(BaseAgent):
    """Base class that exposes only sanitized, typed specialist findings."""

    async def run(self, context: AgentContext) -> AgentResult:
        findings = await self.investigate(context)
        return AgentResult(
            status=AgentExecutionStatus.success,
            payload=findings.model_dump(mode="json"),
        )

    async def investigate(self, context: AgentContext) -> SpecialistFindings:
        raise NotImplementedError

    @staticmethod
    def payload_string(context: AgentContext, key: str) -> str | None:
        value = context.payload.get(key)
        if not isinstance(value, str) or not value.strip():
            return None
        return value.strip()

    @staticmethod
    def payload_dict(context: AgentContext, key: str) -> dict[str, Any] | None:
        value = context.payload.get(key)
        return value if isinstance(value, dict) else None

    @staticmethod
    def unauthenticated(agent_name: str) -> SpecialistFindings:
        return SpecialistFindings(
            specialist=agent_name,
            status=SpecialistStatus.completed,
            confidence=1.0,
            recommendations=(
                "To view or track your personal orders and account details, please log in to your account first. You can sign in using the login options on this page.",
            ),
            evidence_keys=("ref_auth_required_notice",),
        )

    @staticmethod
    def has_verified_session(context: AgentContext) -> bool:
        session = context.visitor_session
        return bool(session and getattr(session, "is_customer_authenticated", False))
