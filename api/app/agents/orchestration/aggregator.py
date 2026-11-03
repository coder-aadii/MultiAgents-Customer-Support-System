"""Evidence aggregation, opaque citation creation, and discrepancy detection."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from app.agents.specialists.schemas import SpecialistFindings, SpecialistStatus
from app.schemas.evidence import EvidenceReference, EvidenceType, TurnEvidenceRegistry, VerifiedFact


@dataclass(frozen=True, slots=True)
class EvidenceConflict:
    entity_type: str
    entity_id: str
    field_name: str
    values: tuple[str, ...]
    evidence_references: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AggregatedEvidence:
    facts: tuple[VerifiedFact, ...]
    citations: tuple[EvidenceReference, ...]
    conflicts: tuple[EvidenceConflict, ...]
    missing_evidence_specialists: tuple[str, ...]
    replan_required: bool
    replan_reason: str | None
    evidence_registry: TurnEvidenceRegistry


class EvidenceAggregator:
    """Combines specialist output without trusting internal evidence identifiers publicly."""

    MAX_REPLAN_ITERATIONS = 2

    def aggregate(
        self,
        tenant_id: uuid.UUID,
        conversation_id: uuid.UUID,
        findings: list[SpecialistFindings],
        replan_attempts_completed: int = 0,
    ) -> AggregatedEvidence:
        registry = TurnEvidenceRegistry(conversation_id)
        opaque_refs: dict[str, str] = {}
        citations: dict[str, EvidenceReference] = {}
        fact_groups: dict[tuple[str, str, str], list[VerifiedFact]] = {}
        deduplicated: dict[tuple[str, str, str, str, str], VerifiedFact] = {}
        missing_evidence: list[str] = []

        for finding in findings:
            if finding.status is SpecialistStatus.completed and not finding.evidence_keys:
                missing_evidence.append(finding.specialist)
            for fact in finding.facts:
                public_ref = self._opaque_reference(
                    tenant_id, conversation_id, fact.source_evidence_id, fact.entity_type
                )
                opaque_refs[fact.source_evidence_id] = public_ref.evidence_id
                citations[public_ref.evidence_id] = public_ref
                normalized = fact.model_copy(update={"source_evidence_id": public_ref.evidence_id})
                identity = (
                    normalized.entity_type,
                    normalized.entity_id,
                    normalized.field_name,
                    str(normalized.field_value),
                    normalized.source_evidence_id,
                )
                deduplicated[identity] = normalized
                fact_groups.setdefault(
                    (normalized.entity_type, normalized.entity_id, normalized.field_name), []
                ).append(normalized)
                registry.register_fact(normalized)

            for evidence_key in finding.evidence_keys:
                entity_type = self._entity_type_for_key(evidence_key)
                public_ref = self._opaque_reference(tenant_id, conversation_id, evidence_key, entity_type)
                opaque_refs[evidence_key] = public_ref.evidence_id
                citations[public_ref.evidence_id] = public_ref

        conflicts = tuple(
            EvidenceConflict(
                entity_type=entity_type,
                entity_id=entity_id,
                field_name=field_name,
                values=tuple(sorted({str(fact.field_value) for fact in grouped_facts})),
                evidence_references=tuple(
                    sorted({fact.source_evidence_id for fact in grouped_facts})
                ),
            )
            for (entity_type, entity_id, field_name), grouped_facts in fact_groups.items()
            if len({str(fact.field_value) for fact in grouped_facts}) > 1
        )
        needs_replan = bool(conflicts or missing_evidence) and (
            replan_attempts_completed < self.MAX_REPLAN_ITERATIONS
        )
        reason = None
        if conflicts:
            reason = "evidence_conflict"
        elif missing_evidence:
            reason = "critical_evidence_missing"

        return AggregatedEvidence(
            facts=tuple(deduplicated.values()),
            citations=tuple(sorted(citations.values(), key=lambda citation: citation.evidence_id)),
            conflicts=conflicts,
            missing_evidence_specialists=tuple(sorted(set(missing_evidence))),
            replan_required=needs_replan,
            replan_reason=reason,
            evidence_registry=registry,
        )

    @staticmethod
    def _opaque_reference(
        tenant_id: uuid.UUID,
        conversation_id: uuid.UUID,
        internal_key: str,
        entity_type: str,
    ) -> EvidenceReference:
        opaque_id = uuid.uuid5(
            uuid.NAMESPACE_URL, f"mcss:{tenant_id}:{conversation_id}:{internal_key}"
        )
        source_type = (
            EvidenceType.knowledge_chunk
            if entity_type == "knowledge_chunk"
            else EvidenceType.business_entity
        )
        return EvidenceReference(
            evidence_id=f"ref_{opaque_id}",
            source_type=source_type,
            title="Verified knowledge evidence" if source_type is EvidenceType.knowledge_chunk else "Verified business evidence",
            entity_type=None if source_type is EvidenceType.knowledge_chunk else entity_type,
        )

    @staticmethod
    def _entity_type_for_key(evidence_key: str) -> str:
        if "chunk" in evidence_key:
            return "knowledge_chunk"
        parts = re.split(r"[:_]", evidence_key.removeprefix("ref_"), maxsplit=1)
        return parts[0] if parts and parts[0] else "business_entity"
