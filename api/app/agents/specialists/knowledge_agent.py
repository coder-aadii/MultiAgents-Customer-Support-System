"""Knowledge-only specialist: RAG policy lookup without customer-data access."""

from __future__ import annotations

from app.agents.base import AgentContext
from app.agents.specialists.base import SpecialistAgent
from app.agents.specialists.schemas import SpecialistFindings, SpecialistStatus
from app.schemas.evidence import VerifiedFact
from app.services.retrieval_service import retrieve_knowledge_chunks


class KnowledgeSpecialistAgent(SpecialistAgent):
    agent_name = "knowledge_specialist"
    agent_version = "1.0"
    system_prompt = "phase_6c_knowledge_specialist_v1"

    def get_capabilities(self) -> list[str]:
        return ["knowledge_policy_retrieval"]

    def get_tool_allowlist(self) -> set[str]:
        return {"retrieve_knowledge_chunks"}

    async def investigate(self, context: AgentContext) -> SpecialistFindings:
        if context.db is None:
            return SpecialistFindings(
                specialist=self.agent_name,
                status=SpecialistStatus.failed,
                failure_reason="DATABASE_SESSION_REQUIRED",
            )
        query = self.payload_string(context, "query") or self.payload_string(context, "user_message")
        if query is None:
            return SpecialistFindings(
                specialist=self.agent_name,
                status=SpecialistStatus.failed,
                failure_reason="KNOWLEDGE_QUERY_REQUIRED",
            )

        response = await retrieve_knowledge_chunks(
            db=context.db,
            query=query,
            organization_id=context.tenant_id,
        )
        if not response.results:
            return SpecialistFindings(
                specialist=self.agent_name,
                status=SpecialistStatus.no_data,
                recommendations=("No matching published policy was found.",),
            )

        facts: list[VerifiedFact] = []
        evidence_keys: list[str] = []
        recommendations: list[str] = []
        for item in response.results:
            evidence_key = f"ref_chunk_{item.chunk_id}"
            evidence_keys.append(evidence_key)
            facts.append(
                VerifiedFact(
                    entity_type="knowledge_chunk",
                    entity_id=str(item.chunk_id),
                    field_name="document_title",
                    field_value=item.document_title,
                    source_evidence_id=evidence_key,
                )
            )
            recommendations.append(f"Review the published policy: {item.document_title}.")
        return SpecialistFindings(
            specialist=self.agent_name,
            status=SpecialistStatus.completed,
            facts=tuple(facts),
            evidence_keys=tuple(evidence_keys),
            confidence=max(item.score for item in response.results),
            recommendations=tuple(recommendations),
        )
