"""Read-only specialist that consolidates evidence-backed findings into plain text."""

from __future__ import annotations

import re

from app.agents.base import AgentContext
from app.agents.specialists.base import SpecialistAgent
from app.agents.specialists.schemas import SpecialistFindings, SpecialistStatus
from app.schemas.evidence import VerifiedFact
from app.services.policy_validator import PolicyValidator


class ResolutionSynthesisAgent(SpecialistAgent):
    agent_name = "resolution_synthesis_agent"
    agent_version = "1.0"
    system_prompt = "phase_6c_resolution_synthesis_agent_v1"
    _UNTRUSTED_INSTRUCTION = re.compile(
        r"(?i)(system\s+instruction|ignore\s+(?:all\s+)?previous|assistant\s*:|tool\s+call)"
    )

    def get_capabilities(self) -> list[str]:
        return ["evidence_grounded_resolution_synthesis"]

    def get_tool_allowlist(self) -> set[str]:
        return set()

    async def investigate(self, context: AgentContext) -> SpecialistFindings:
        raw_findings = context.payload.get("findings")
        if not isinstance(raw_findings, list):
            return SpecialistFindings(
                specialist=self.agent_name,
                status=SpecialistStatus.failed,
                failure_reason="SPECIALIST_FINDINGS_REQUIRED",
            )
        try:
            findings = [SpecialistFindings.model_validate(item) for item in raw_findings]
        except (TypeError, ValueError):
            return SpecialistFindings(
                specialist=self.agent_name,
                status=SpecialistStatus.failed,
                failure_reason="SPECIALIST_FINDINGS_INVALID",
            )

        facts: list[VerifiedFact] = [fact for finding in findings for fact in finding.facts]
        recommendations = tuple(
            recommendation
            for finding in findings
            if finding.status is SpecialistStatus.completed
            for recommendation in finding.recommendations
        )
        if not recommendations:
            return SpecialistFindings(
                specialist=self.agent_name,
                status=SpecialistStatus.no_data,
                recommendations=("We could not verify enough information to provide a final resolution.",),
                failure_reason="NO_COMPLETED_FINDINGS",
            )

        safe_recommendations = tuple(
            recommendation
            for recommendation in recommendations
            if not self._UNTRUSTED_INSTRUCTION.search(recommendation)
        )
        if not safe_recommendations:
            return SpecialistFindings(
                specialist=self.agent_name,
                status=SpecialistStatus.failed,
                facts=tuple(facts),
                recommendations=("A support specialist will review the verified information.",),
                failure_reason="UNTRUSTED_INSTRUCTION_CONTENT",
            )
        response_text = " ".join(safe_recommendations)
        is_grounded, reason = PolicyValidator.validate_response_grounding(response_text, facts)
        if not is_grounded:
            return SpecialistFindings(
                specialist=self.agent_name,
                status=SpecialistStatus.failed,
                facts=tuple(facts),
                recommendations=("A support specialist will review the verified information.",),
                failure_reason=f"POLICY_GROUNDING_FAILED:{reason}",
            )
        evidence_keys = tuple(
            key for finding in findings for key in finding.evidence_keys
        )
        return SpecialistFindings(
            specialist=self.agent_name,
            status=SpecialistStatus.completed,
            facts=tuple(facts),
            evidence_keys=evidence_keys,
            confidence=min((finding.confidence for finding in findings), default=0.0),
            recommendations=(response_text,),
        )
