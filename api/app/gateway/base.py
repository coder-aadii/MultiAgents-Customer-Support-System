from __future__ import annotations

import abc
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.evidence import EvidenceRecordCreate, EvidenceType, VerifiedFact


class GatewayError(Exception):
    """Base exception for all tool gateway and connector operations."""


class ConnectorError(GatewayError):
    """External connector execution, networking, or integration error."""


class EntityNotFoundError(GatewayError):
    """Raised when an entity does not exist or does not belong to the requesting customer.

    Raising this for both missing and unauthorized entities prevents customer enumeration (BOLA/IDOR).
    """


class ToolAuthorizationError(GatewayError):
    """Raised when the session identity assurance level is insufficient (< 2) or token is expired/revoked."""


class ToolTimeoutError(GatewayError):
    """Raised when a connector call exceeds its bounded timeout."""


class ToolValidationError(GatewayError):
    """Raised when input parameters fail schema validation or contain prohibited claims."""


class CapabilityDefinition(BaseModel):
    """Metadata describing a connector capability or business tool."""

    name: str = Field(description="Unique capability name (e.g. 'get_order_summary')")
    description: str = Field(description="Human-readable description of what the tool retrieves")
    entity_type: str = Field(description="Domain-agnostic entity type (e.g. 'order', 'shipment')")
    parameters_schema: dict[str, Any] = Field(
        default_factory=dict,
        description="JSON schema describing accepted parameters",
    )
    requires_ial: int = Field(
        default=2,
        description="Minimum Identity Assurance Level required (defaults to 2: Cryptographically Verified)",
    )

    model_config = ConfigDict(frozen=True)


class EntityRecord(BaseModel):
    """Standardized, domain-agnostic representation of a business entity retrieved from a connector."""

    entity_type: str = Field(description="Type of entity (e.g. 'order', 'shipment', 'refund')")
    entity_id: str = Field(description="Unique entity identifier (e.g. 'ORD-1001')")
    customer_id: str = Field(description="Verified customer subject ID owning this entity")
    title: str = Field(description="Safe display title for UI citation (e.g. 'Order #ORD-1001')")
    summary_fields: dict[str, Any] = Field(
        default_factory=dict,
        description="Normalized key-value attributes for fact extraction (e.g. status, total_amount)",
    )
    raw_payload: dict[str, Any] = Field(
        default_factory=dict,
        description="Complete raw record from system of record (stored server-side only)",
    )
    retrieved_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp when record was retrieved",
    )

    model_config = ConfigDict(frozen=True)

    def to_evidence_record_create(self, expires_in_days: int = 30) -> EvidenceRecordCreate:
        """Converts this entity record into a server-side EvidenceRecordCreate schema."""
        return EvidenceRecordCreate(
            evidence_key=f"{self.entity_type}:{self.entity_id}",
            source_type=EvidenceType.business_entity,
            title=self.title,
            entity_type=self.entity_type,
            payload_json={
                "entity_id": self.entity_id,
                "summary_fields": self.summary_fields,
                "raw": self.raw_payload,
            },
            verified_customer_id=self.customer_id,
            expires_in_days=expires_in_days,
        )

    def extract_facts(self, source_evidence_id: str) -> list[VerifiedFact]:
        """Extracts discrete scalar VerifiedFact instances from normalized summary fields."""
        facts: list[VerifiedFact] = []
        for key, val in self.summary_fields.items():
            if isinstance(val, (str, int, float)) and not isinstance(val, bool):
                facts.append(
                    VerifiedFact(
                        entity_type=self.entity_type,
                        entity_id=self.entity_id,
                        field_name=key,
                        field_value=val,
                        source_evidence_id=source_evidence_id,
                    )
                )
            elif isinstance(val, bool):
                facts.append(
                    VerifiedFact(
                        entity_type=self.entity_type,
                        entity_id=self.entity_id,
                        field_name=key,
                        field_value=str(val).lower(),
                        source_evidence_id=source_evidence_id,
                    )
                )
        return facts


class BaseConnector(abc.ABC):
    """Abstract base contract for all domain-agnostic enterprise data connectors."""

    @property
    @abc.abstractmethod
    def connector_name(self) -> str:
        """Unique identifier for this connector implementation."""
        raise NotImplementedError

    @property
    @abc.abstractmethod
    def supported_entity_types(self) -> list[str]:
        """List of entity types supported by this connector (e.g. ['order', 'shipment'])."""
        raise NotImplementedError

    @abc.abstractmethod
    def get_capabilities(self) -> list[CapabilityDefinition]:
        """Returns the capabilities exposed by this connector."""
        raise NotImplementedError

    @abc.abstractmethod
    async def get_entity_summary(
        self, entity_type: str, entity_id: str, customer_id: str
    ) -> EntityRecord:
        """Retrieves a single business entity, strictly asserting customer ownership.

        Raises EntityNotFoundError if entity does not exist or does not belong to customer_id.
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def list_customer_entities(
        self, entity_type: str, customer_id: str
    ) -> list[EntityRecord]:
        """Retrieves all business entities of the given type owned by customer_id."""
        raise NotImplementedError

