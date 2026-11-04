"""Knowledge-only specialist: RAG policy lookup without customer-data access."""

from __future__ import annotations

import logging
import re

from app.agents.base import AgentContext
from app.agents.specialists.base import SpecialistAgent
from app.agents.specialists.schemas import SpecialistFindings, SpecialistStatus
from app.providers import LLMMessage, LLMProvider, LLMRequest, get_llm_provider
from app.schemas.evidence import VerifiedFact
from app.schemas.retrieval import RetrievalResultItem
from app.services.policy_validator import PolicyValidator
from app.services.retrieval_service import retrieve_knowledge_chunks

logger = logging.getLogger(__name__)

KNOWLEDGE_EXPLAINER_PROMPT = """You are a customer support knowledge specialist.
Explain the relevant policy or knowledge article content directly and concisely to answer the customer's query, based strictly on the provided policy excerpts.
Rules:
1. Answer in 1 to 3 clear, natural sentences.
2. Explain the actual policy rules, definitions, conditions, or meanings from the text.
3. Do NOT say "Review the published policy" or tell the user to read an article. Explain the content directly.
4. Do NOT mention whether you have access to account data or order records (other specialists handle records).
5. Never invent facts, entity IDs, dates, or dollar amounts not in the source text.
"""


class KnowledgeSpecialistAgent(SpecialistAgent):
    agent_name = "knowledge_specialist"
    agent_version = "1.0"
    system_prompt = "phase_6c_knowledge_specialist_v1"

    def __init__(self, llm_provider: LLMProvider | None = None) -> None:
        self._provider = llm_provider

    def get_capabilities(self) -> list[str]:
        return ["knowledge_policy_retrieval"]

    def get_tool_allowlist(self) -> set[str]:
        return {"retrieve_knowledge_chunks"}

    @staticmethod
    def _extract_clean_excerpt(item: RetrievalResultItem, query: str = "") -> str:
        """Extract substantive article content directly from chunk, filtering metadata."""
        lines = item.content.split("\n")
        substantive: list[str] = []
        for line in lines:
            trimmed = line.strip()
            if not trimmed:
                continue
            if trimmed.startswith(("#", ">", "---", "***", "===")):
                continue
            if re.match(r"^\*\*(?:Document ID|Category|Audience|Version|Status|Purpose)\b", trimmed, re.IGNORECASE):
                continue
            cleaned = re.sub(r"^[-*]\s+", "", trimmed)
            cleaned = re.sub(r"`([^`]+)`", r"\1", cleaned)
            if cleaned:
                substantive.append(cleaned)

        if not substantive:
            return f"Under {item.document_title}: {item.content[:200].strip()}"

        keywords = [
            w.lower()
            for w in re.findall(r"\w{4,}", query)
            if w.lower() not in {"what", "where", "order", "status", "mean", "does", "have"}
        ]
        matched_lines = [s for s in substantive if any(k in s.lower() for k in keywords)] if keywords else []
        selected = matched_lines if matched_lines else substantive

        combined: list[str] = []
        total_len = 0
        for text in selected:
            if total_len + len(text) > 300 and combined:
                break
            combined.append(text)
            total_len += len(text)

        body = " ".join(combined).strip()
        return f"According to {item.document_title}: {body}"

    _DISCLAIMER_REGEX = re.compile(
        r"^(?:(?:I (?:cannot|can't|do not have|am unable to|don't have)\s+(?:access|view|retrieve)[^.]*\.)|(?:As an AI[^.]*\.)|(?:I am not able to[^.]*\.))\s*",
        re.IGNORECASE,
    )

    async def _synthesize_explanation(
        self,
        query: str,
        results: list[RetrievalResultItem],
        facts: list[VerifiedFact],
        known_status: str | None = None,
    ) -> str:
        sources_text = "\n\n".join(
            f"Policy: {item.document_title}\n{item.content.strip()}"
            for item in results[:3]
        )
        provider = self._provider or get_llm_provider()
        user_prompt = f"Customer Query: {query}"
        if known_status:
            user_prompt += f"\nKnown order status to explain: {known_status}"
        user_prompt += f"\n\nPublished Policy Sources:\n{sources_text}"
        try:
            req = LLMRequest(
                messages=[
                    LLMMessage(role="system", content=KNOWLEDGE_EXPLAINER_PROMPT.strip()),
                    LLMMessage(role="user", content=user_prompt),
                ],
                temperature=0.0,
                max_tokens=256,
                timeout=6.0,
            )
            res = await provider.chat_complete(req)
            explanation = res.content.strip()
            explanation = self._DISCLAIMER_REGEX.sub("", explanation).strip()
            if explanation and not explanation.startswith("Deterministic response"):
                is_grounded, _ = PolicyValidator.validate_response_grounding(explanation, facts)
                if is_grounded:
                    return explanation
        except Exception as exc:
            logger.warning("Knowledge specialist LLM explanation failed: %s", exc)

        return self._extract_clean_excerpt(results[0], query=query)

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

        known_status = self.payload_string(context, "order_status") or self.payload_string(context, "status")
        if not known_status:
            dep_results = context.payload.get("dependency_results", {})
            if isinstance(dep_results, dict):
                for dep_data in dep_results.values():
                    if isinstance(dep_data, dict):
                        payload = dep_data.get("payload", {})
                        if isinstance(payload, dict):
                            for f in payload.get("facts", []):
                                if isinstance(f, dict) and f.get("field_name") == "status":
                                    known_status = str(f.get("field_value"))
                                    break

        retrieval_query = query
        if known_status and known_status.lower() not in query.lower():
            retrieval_query = f"{query} {known_status}"

        response = await retrieve_knowledge_chunks(
            db=context.db,
            query=retrieval_query,
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
            facts.append(
                VerifiedFact(
                    entity_type="knowledge_chunk",
                    entity_id=str(item.chunk_id),
                    field_name="content",
                    field_value=item.content,
                    source_evidence_id=evidence_key,
                )
            )

        explanation = await self._synthesize_explanation(
            query, response.results, facts, known_status=known_status
        )

        return SpecialistFindings(
            specialist=self.agent_name,
            status=SpecialistStatus.completed,
            facts=tuple(facts),
            evidence_keys=tuple(evidence_keys),
            confidence=max(item.score for item in response.results),
            recommendations=(explanation,),
        )
