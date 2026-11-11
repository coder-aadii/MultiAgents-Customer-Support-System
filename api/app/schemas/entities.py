from __future__ import annotations

import re
from typing import Any
from pydantic import BaseModel, ConfigDict, Field, field_validator


class EntityFieldDefinition(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(..., min_length=1, max_length=64)
    field_type: str = Field(default="string", max_length=32)
    description: str | None = Field(default=None, max_length=256)
    required: bool = False
    is_identifier: bool = False


class EntityDefinition(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(..., min_length=1, max_length=64)
    display_name: str = Field(..., min_length=1, max_length=128)
    description: str = Field(default="", max_length=512)
    identifier_pattern: str | None = Field(default=None, max_length=256)
    primary_key: str = Field(default="id", max_length=64)
    fields: list[EntityFieldDefinition] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=lambda: ["read"])

    @field_validator("name")
    @classmethod
    def validate_name_slug(cls, v: str) -> str:
        clean = v.strip().lower()
        if not re.match(r"^[a-z0-9_-]+$", clean):
            raise ValueError("Entity name must contain only lowercase alphanumeric characters, underscores, or hyphens.")
        return clean

    @field_validator("identifier_pattern")
    @classmethod
    def validate_regex_pattern(cls, v: str | None) -> str | None:
        if v is not None and v.strip():
            try:
                re.compile(v)
            except re.error as e:
                raise ValueError(f"Invalid regular expression pattern: {e}")
            return v
        return None


class ActionPolicySchema(BaseModel):
    model_config = ConfigDict(extra="ignore")

    action_type: str = Field(..., min_length=1, max_length=64)
    description: str = Field(..., min_length=1, max_length=256)
    entity_type: str = Field(..., min_length=1, max_length=64)
    requires_approval: bool = True
    allowed_roles: list[str] = Field(default_factory=lambda: ["admin", "owner"])


class OrganizationEntitySchema(BaseModel):
    model_config = ConfigDict(extra="ignore")

    entities: list[EntityDefinition] = Field(default_factory=list)
    actions: list[ActionPolicySchema] = Field(default_factory=list)

    def get_entity(self, name: str) -> EntityDefinition | None:
        name_clean = name.strip().lower()
        for ent in self.entities:
            if ent.name == name_clean:
                return ent
        return None

    def get_pattern_map(self) -> dict[str, re.Pattern[str]]:
        """Compiles identifier regexes for each entity that has one defined."""
        patterns: dict[str, re.Pattern[str]] = {}
        for ent in self.entities:
            if ent.identifier_pattern:
                try:
                    patterns[ent.name] = re.compile(ent.identifier_pattern, re.IGNORECASE)
                except re.error:
                    continue
        return patterns


def get_default_olist_entity_schema() -> OrganizationEntitySchema:
    """Default Olist e-commerce entity definitions for backwards compatibility."""
    return OrganizationEntitySchema(
        entities=[
            EntityDefinition(
                name="order",
                display_name="Customer Order",
                description="E-commerce purchase order with payment, status, and item details.",
                identifier_pattern=r"\b(?:ORD-[0-9A-Za-z]{3,32}|[0-9a-fA-F]{32}|#[0-9A-Za-z]{2,32}|\d{3,16})\b",
                primary_key="order_id",
                capabilities=["read", "cancel"],
                fields=[
                    EntityFieldDefinition(name="order_id", field_type="string", required=True, is_identifier=True),
                    EntityFieldDefinition(name="status", field_type="string", required=True),
                    EntityFieldDefinition(name="total_amount", field_type="float"),
                    EntityFieldDefinition(name="customer_id", field_type="string"),
                ],
            ),
            EntityDefinition(
                name="shipment",
                display_name="Fulfillment Shipment",
                description="Logistics carrier shipment with tracking code and delivery milestones.",
                identifier_pattern=r"\b(?:TRK|SHP|BR)-[0-9A-Z]{3,32}\b",
                primary_key="tracking_number",
                capabilities=["read"],
                fields=[
                    EntityFieldDefinition(name="tracking_number", field_type="string", required=True, is_identifier=True),
                    EntityFieldDefinition(name="carrier", field_type="string"),
                    EntityFieldDefinition(name="status", field_type="string"),
                    EntityFieldDefinition(name="estimated_delivery", field_type="datetime"),
                ],
            ),
            EntityDefinition(
                name="refund",
                display_name="Payment Refund",
                description="Payment reversal or charge credit transaction record.",
                identifier_pattern=r"\bREF-[0-9A-Z]{3,32}\b",
                primary_key="refund_id",
                capabilities=["read", "approve"],
                fields=[
                    EntityFieldDefinition(name="refund_id", field_type="string", required=True, is_identifier=True),
                    EntityFieldDefinition(name="order_id", field_type="string", required=True),
                    EntityFieldDefinition(name="amount", field_type="float", required=True),
                    EntityFieldDefinition(name="status", field_type="string"),
                ],
            ),
            EntityDefinition(
                name="customer",
                display_name="Customer Profile",
                description="Verified customer identity, email, and contact record.",
                identifier_pattern=r"\b(?:CUST-[0-9A-Za-z]{3,32}|cust_[0-9a-zA-Z_]+)\b",
                primary_key="customer_id",
                capabilities=["read"],
                fields=[
                    EntityFieldDefinition(name="customer_id", field_type="string", required=True, is_identifier=True),
                    EntityFieldDefinition(name="email", field_type="string", required=True),
                    EntityFieldDefinition(name="name", field_type="string"),
                ],
            ),
        ],
        actions=[
            ActionPolicySchema(
                action_type="cancel_order",
                description="Cancel an active processing order prior to warehouse dispatch.",
                entity_type="order",
                requires_approval=True,
                allowed_roles=["admin", "owner", "supervisor"],
            ),
            ActionPolicySchema(
                action_type="process_refund",
                description="Issue payment reversal for returned or damaged goods.",
                entity_type="refund",
                requires_approval=True,
                allowed_roles=["admin", "owner"],
            ),
        ],
    )


def get_default_cloudpulse_entity_schema() -> OrganizationEntitySchema:
    """Default B2B SaaS entity schema (CloudPulse infrastructure platform)."""
    return OrganizationEntitySchema(
        entities=[
            EntityDefinition(
                name="workspace",
                display_name="Cloud Workspace",
                description="B2B tenant cloud workspace environment and project container.",
                identifier_pattern=r"\b(?:WS-[0-9a-zA-Z]{4,16}|ws_[0-9a-zA-Z_]+)\b",
                primary_key="workspace_id",
                capabilities=["read", "manage"],
                fields=[
                    EntityFieldDefinition(name="workspace_id", field_type="string", required=True, is_identifier=True),
                    EntityFieldDefinition(name="name", field_type="string"),
                    EntityFieldDefinition(name="tier", field_type="string"),
                    EntityFieldDefinition(name="retention_days", field_type="integer"),
                    EntityFieldDefinition(name="status", field_type="string"),
                ],
            ),
            EntityDefinition(
                name="subscription",
                display_name="Service Subscription",
                description="Account billing tier, seat limits, and renewal commitments.",
                identifier_pattern=r"\b(?:SUB-[0-9a-zA-Z]{4,16})\b",
                primary_key="subscription_id",
                capabilities=["read"],
                fields=[
                    EntityFieldDefinition(name="subscription_id", field_type="string", required=True, is_identifier=True),
                    EntityFieldDefinition(name="tier", field_type="string"),
                    EntityFieldDefinition(name="seat_count", field_type="integer"),
                    EntityFieldDefinition(name="renewal_date", field_type="string"),
                ],
            ),
            EntityDefinition(
                name="api_quota",
                display_name="API Quota",
                description="Monthly data ingestion limit, retention, and rate limit utilization.",
                identifier_pattern=r"\b(?:QUOTA-[0-9a-zA-Z]{4,16})\b",
                primary_key="quota_id",
                capabilities=["read"],
                fields=[
                    EntityFieldDefinition(name="quota_id", field_type="string", required=True, is_identifier=True),
                    EntityFieldDefinition(name="monthly_limit", field_type="string"),
                    EntityFieldDefinition(name="usage_percent", field_type="float"),
                    EntityFieldDefinition(name="retention_days", field_type="integer"),
                ],
            ),
        ],
        actions=[
            ActionPolicySchema(
                action_type="regenerate_api_key",
                description="Rotate and reissue production API authentication key.",
                entity_type="workspace",
                requires_approval=True,
                allowed_roles=["admin", "owner"],
            ),
            ActionPolicySchema(
                action_type="increase_seat_limit",
                description="Expand maximum active user seats on enterprise subscription.",
                entity_type="subscription",
                requires_approval=True,
                allowed_roles=["admin"],
            ),
        ],
    )


def get_default_apex_bank_entity_schema() -> OrganizationEntitySchema:
    """Default Fintech / Banking entity schema (Apex Global Bank)."""
    return OrganizationEntitySchema(
        entities=[
            EntityDefinition(
                name="account",
                display_name="Bank Account",
                description="Commercial or retail deposit, checking, or savings account.",
                identifier_pattern=r"\b(?:ACC-[0-9]{4,16})\b",
                primary_key="account_id",
                capabilities=["read"],
                fields=[
                    EntityFieldDefinition(name="account_id", field_type="string", required=True, is_identifier=True),
                    EntityFieldDefinition(name="account_type", field_type="string"),
                    EntityFieldDefinition(name="balance", field_type="float"),
                    EntityFieldDefinition(name="currency", field_type="string"),
                    EntityFieldDefinition(name="status", field_type="string"),
                ],
            ),
            EntityDefinition(
                name="transaction",
                display_name="Financial Transaction",
                description="Posted debit, credit, or card payment ledger record.",
                identifier_pattern=r"\b(?:TXN-[0-9A-Za-z]{4,20})\b",
                primary_key="transaction_id",
                capabilities=["read", "dispute"],
                fields=[
                    EntityFieldDefinition(name="transaction_id", field_type="string", required=True, is_identifier=True),
                    EntityFieldDefinition(name="account_id", field_type="string"),
                    EntityFieldDefinition(name="amount", field_type="float"),
                    EntityFieldDefinition(name="merchant", field_type="string"),
                    EntityFieldDefinition(name="timestamp", field_type="datetime"),
                    EntityFieldDefinition(name="status", field_type="string"),
                ],
            ),
            EntityDefinition(
                name="wire_transfer",
                display_name="Wire Transfer",
                description="SWIFT / Fedwire international or domestic fund transfer.",
                identifier_pattern=r"\b(?:WIRE-[0-9A-Za-z]{4,20})\b",
                primary_key="wire_id",
                capabilities=["read"],
                fields=[
                    EntityFieldDefinition(name="wire_id", field_type="string", required=True, is_identifier=True),
                    EntityFieldDefinition(name="recipient", field_type="string"),
                    EntityFieldDefinition(name="amount", field_type="float"),
                    EntityFieldDefinition(name="status", field_type="string"),
                ],
            ),
        ],
        actions=[
            ActionPolicySchema(
                action_type="dispute_transaction",
                description="Initiate chargeback dispute investigation for unverified transaction.",
                entity_type="transaction",
                requires_approval=True,
                allowed_roles=["admin", "supervisor"],
            ),
            ActionPolicySchema(
                action_type="freeze_card",
                description="Temporarily lock compromised debit/credit card.",
                entity_type="account",
                requires_approval=True,
                allowed_roles=["admin", "staff"],
            ),
        ],
    )


def get_default_medicare_entity_schema() -> OrganizationEntitySchema:
    """Default Healthcare / Telehealth entity schema (MediCare portal)."""
    return OrganizationEntitySchema(
        entities=[
            EntityDefinition(
                name="patient",
                display_name="Patient Record",
                description="Verified patient medical demographics and assigned clinician.",
                identifier_pattern=r"\b(?:PAT-[0-9A-Za-z]{4,16})\b",
                primary_key="patient_id",
                capabilities=["read"],
                fields=[
                    EntityFieldDefinition(name="patient_id", field_type="string", required=True, is_identifier=True),
                    EntityFieldDefinition(name="name", field_type="string"),
                    EntityFieldDefinition(name="primary_doctor", field_type="string"),
                ],
            ),
            EntityDefinition(
                name="appointment",
                display_name="Clinic Appointment",
                description="Scheduled doctor visit or telehealth consultation.",
                identifier_pattern=r"\b(?:APT-[0-9A-Za-z]{4,16})\b",
                primary_key="appointment_id",
                capabilities=["read", "reschedule"],
                fields=[
                    EntityFieldDefinition(name="appointment_id", field_type="string", required=True, is_identifier=True),
                    EntityFieldDefinition(name="doctor", field_type="string"),
                    EntityFieldDefinition(name="datetime", field_type="datetime"),
                    EntityFieldDefinition(name="department", field_type="string"),
                    EntityFieldDefinition(name="status", field_type="string"),
                ],
            ),
            EntityDefinition(
                name="prescription",
                display_name="Prescription Refill",
                description="Active pharmaceutical medication prescription and refill balance.",
                identifier_pattern=r"\b(?:RX-[0-9A-Za-z]{4,16})\b",
                primary_key="prescription_id",
                capabilities=["read", "refill"],
                fields=[
                    EntityFieldDefinition(name="prescription_id", field_type="string", required=True, is_identifier=True),
                    EntityFieldDefinition(name="medication", field_type="string"),
                    EntityFieldDefinition(name="dosage", field_type="string"),
                    EntityFieldDefinition(name="refills_remaining", field_type="integer"),
                    EntityFieldDefinition(name="status", field_type="string"),
                ],
            ),
        ],
        actions=[
            ActionPolicySchema(
                action_type="request_refill",
                description="Submit doctor approval request for prescription medication refill.",
                entity_type="prescription",
                requires_approval=True,
                allowed_roles=["doctor", "nurse", "admin"],
            ),
        ],
    )
