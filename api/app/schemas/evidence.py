from __future__ import annotations

import enum
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EvidenceType(str, enum.Enum):
    """Categorization of evidence backing assistant statements."""

    knowledge_chunk = "knowledge_chunk"
    business_entity = "business_entity"


class EvidenceReference(BaseModel):
    """Public, privacy-preserving evidence citation reference exposed in message payloads.

    Invariants:
    - Never includes raw entity payloads, database dumps, customer addresses, or sensitive fields.
    - Uses server-generated opaque evidence_id references.
    - Preserves backward compatibility with Phase 0-4 knowledge citations via chunk_id and doc_id.
    """

    evidence_id: str = Field(
        description="Server-generated opaque reference ID (e.g. ref_9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d)"
    )
    source_type: EvidenceType = Field(
        description="Source category: knowledge_chunk or business_entity"
    )
    title: str = Field(
        description="Safe public display title (e.g. 'Return Policy', 'Order #ORD-1024')"
    )
    entity_type: str | None = Field(
        default=None,
        description="Generic business entity type if applicable (e.g. 'order', 'shipment', 'ticket')",
    )

    # Backward compatibility attributes for Phase 0-4 knowledge base frontend lookups
    chunk_id: str | None = Field(
        default=None,
        description="Knowledge chunk UUID string for existing UI components",
    )
    doc_id: str | None = Field(
        default=None,
        description="Knowledge document UUID string for existing UI components",
    )

    model_config = ConfigDict(frozen=True, populate_by_name=True)


class VerifiedFact(BaseModel):
    """Strongly typed atomic fact registered by connectors and validated by policy."""

    entity_type: str = Field(description="Domain-agnostic entity type (e.g. 'order', 'shipment')")
    entity_id: str = Field(description="Entity identifier (e.g. 'ORD-1024')")
    field_name: str = Field(description="Attribute name (e.g. 'status', 'estimated_delivery')")
    field_value: str | int | float = Field(
        description="Verified scalar value (e.g. 'delivered', '2026-11-05', 89.50)"
    )
    source_evidence_id: str = Field(
        description="ID of the EvidenceRecord containing source proof"
    )

    model_config = ConfigDict(frozen=True)


class EvidenceRecordCreate(BaseModel):
    """Internal schema for persisting server-side evidence records."""

    evidence_key: str = Field(
        description="Scoped unique identifier within conversation (e.g. 'order:ORD-1024', 'chunk:<uuid>')"
    )
    source_type: EvidenceType = Field(description="Source type")
    title: str = Field(description="Safe display title")
    entity_type: str | None = Field(default=None, description="Entity type")
    payload_json: dict[str, Any] = Field(
        description="Protected raw entity or chunk payload stored securely on server"
    )
    verified_customer_id: str | None = Field(
        default=None,
        description="Verified customer subject ID binding for access control",
    )
    expires_in_days: int = Field(
        default=30,
        description="Retention lifetime in days before expiration",
    )


class TurnEvidenceRegistry:
    """Per-turn in-memory evidence registry used during assistant processing."""

    def __init__(self, conversation_id: uuid.UUID) -> None:
        self.conversation_id = conversation_id
        self._records: list[EvidenceRecordCreate] = []
        self._facts: list[VerifiedFact] = []

    @property
    def records(self) -> list[EvidenceRecordCreate]:
        return list(self._records)

    @property
    def facts(self) -> list[VerifiedFact]:
        return list(self._facts)

    def register_record(self, record: EvidenceRecordCreate) -> None:
        self._records.append(record)

    def register_fact(self, fact: VerifiedFact) -> None:
        self._facts.append(fact)

