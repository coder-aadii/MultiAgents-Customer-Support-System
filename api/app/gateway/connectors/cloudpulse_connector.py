"""Deterministic CloudPulse SaaS connector for SaaS workspace, subscription, and quota lookups."""

from __future__ import annotations

from typing import Any

from app.gateway.base import (
    BaseConnector,
    CapabilityDefinition,
    EntityNotFoundError,
    EntityRecord,
)


class CloudPulseSaaSConnector(BaseConnector):
    """Mock connector representing a modern SaaS B2B customer support backend (CloudPulse).

    Supplies fixtures for workspaces, team subscriptions, and API quotas.
    Enforces strict customer ownership and IDOR/BOLA isolation.
    """

    _NAME = "cloudpulse_saas"
    _SUPPORTED_TYPES = ["workspace", "subscription", "api_quota"]

    def __init__(self, custom_records: list[dict[str, Any]] | None = None) -> None:
        self._records_by_key: dict[str, dict[str, Any]] = {}
        self._seed_default_fixtures()
        if custom_records:
            for rec in custom_records:
                key = self._build_key(rec["entity_type"], rec["entity_id"])
                self._records_by_key[key] = rec

    @property
    def connector_name(self) -> str:
        return self._NAME

    @property
    def supported_entity_types(self) -> list[str]:
        return list(self._SUPPORTED_TYPES)

    def _build_key(self, entity_type: str, entity_id: str, customer_id: str | None = None) -> str:
        base = f"{entity_type.strip().lower()}:{entity_id.strip().upper()}"
        if customer_id:
            return f"{base}:{customer_id.strip()}"
        return base

    def _seed_default_fixtures(self) -> None:
        fixtures: list[dict[str, Any]] = [
            # Workspace for customer 123
            {
                "entity_type": "workspace",
                "entity_id": "WS-101",
                "customer_id": "cust_verified_123",
                "title": "CloudPulse Workspace WS-101",
                "summary_fields": {
                    "name": "Acme Core Dev",
                    "status": "active",
                    "tier": "Enterprise",
                    "members_count": 42,
                    "region": "us-east-1",
                    "api_calls_today": 12450,
                },
                "raw_payload": {
                    "workspace_id": "WS-101",
                    "customer_id": "cust_verified_123",
                    "created_at": "2026-01-15T00:00:00Z",
                    "billing_email": "billing@acme.example.com",
                },
            },
            {
                "entity_type": "workspace",
                "entity_id": "WS-102",
                "customer_id": "cust_verified_123",
                "title": "CloudPulse Workspace WS-102",
                "summary_fields": {
                    "name": "Staging Sandbox",
                    "status": "provisioning",
                    "tier": "Developer",
                    "members_count": 5,
                    "region": "eu-west-1",
                },
                "raw_payload": {
                    "workspace_id": "WS-102",
                    "customer_id": "cust_verified_123",
                },
            },
            # Foreign workspace owned by customer 456 (used for BOLA negative testing)
            {
                "entity_type": "workspace",
                "entity_id": "WS-999",
                "customer_id": "cust_foreign_456",
                "title": "Foreign Corp Workspace",
                "summary_fields": {
                    "name": "Foreign Dev",
                    "status": "active",
                    "tier": "Enterprise",
                },
                "raw_payload": {
                    "workspace_id": "WS-999",
                    "customer_id": "cust_foreign_456",
                },
            },
            # Subscription for customer 123
            {
                "entity_type": "subscription",
                "entity_id": "SUB-2001",
                "customer_id": "cust_verified_123",
                "title": "CloudPulse Enterprise Subscription",
                "summary_fields": {
                    "status": "active",
                    "tier": "Enterprise",
                    "plan": "Enterprise Annual",
                    "seats_allocated": 50,
                    "renewal_date": "2027-01-01",
                    "monthly_cost": 499.0,
                },
                "raw_payload": {
                    "subscription_id": "SUB-2001",
                    "customer_id": "cust_verified_123",
                },
            },
            # API Quota for customer 123
            {
                "entity_type": "api_quota",
                "entity_id": "QUOTA-301",
                "customer_id": "cust_verified_123",
                "title": "Production API Quota",
                "summary_fields": {
                    "status": "healthy",
                    "used_percentage": 68.5,
                    "rate_limit_per_minute": 5000,
                    "monthly_limit": 1000000,
                },
                "raw_payload": {
                    "quota_id": "QUOTA-301",
                    "customer_id": "cust_verified_123",
                },
            },
            # Showcase user_99 (Alex Rivera) fixtures
            {
                "entity_type": "workspace",
                "entity_id": "WS-771a8b",
                "customer_id": "user_99",
                "title": "CloudPulse Workspace #WS-771a8b",
                "summary_fields": {
                    "name": "CloudPulse Core Cluster",
                    "status": "active",
                    "tier": "Enterprise",
                    "retention_days": 90,
                    "members_count": 35,
                    "region": "us-east-1",
                },
                "raw_payload": {
                    "workspace_id": "WS-771a8b",
                    "customer_id": "user_99",
                    "created_at": "2025-10-01T00:00:00Z",
                },
            },
            {
                "entity_type": "workspace",
                "entity_id": "WS-992b1c",
                "customer_id": "user_99",
                "title": "CloudPulse Workspace #WS-992b1c",
                "summary_fields": {
                    "name": "Staging Sandbox",
                    "status": "active",
                    "tier": "Developer",
                    "retention_days": 30,
                    "members_count": 8,
                    "region": "eu-central-1",
                },
                "raw_payload": {
                    "workspace_id": "WS-992b1c",
                    "customer_id": "user_99",
                },
            },
            {
                "entity_type": "subscription",
                "entity_id": "SUB-2001",
                "customer_id": "user_99",
                "title": "CloudPulse Enterprise Subscription",
                "summary_fields": {
                    "status": "active",
                    "tier": "Enterprise",
                    "plan": "Enterprise Annual",
                    "seats_allocated": 50,
                    "renewal_date": "2027-01-01",
                    "monthly_cost": 499.0,
                },
                "raw_payload": {
                    "subscription_id": "SUB-2001",
                    "customer_id": "user_99",
                },
            },
            {
                "entity_type": "api_quota",
                "entity_id": "QUOTA-301",
                "customer_id": "user_99",
                "title": "Monthly Ingestion API Quota",
                "summary_fields": {
                    "status": "healthy",
                    "used_percentage": 68.5,
                    "rate_limit_per_minute": 5000,
                    "monthly_limit": 50000000,
                },
                "raw_payload": {
                    "quota_id": "QUOTA-301",
                    "customer_id": "user_99",
                },
            },
        ]
        for fix in fixtures:
            scoped_key = self._build_key(fix["entity_type"], fix["entity_id"], fix["customer_id"])
            self._records_by_key[scoped_key] = fix
            generic_key = self._build_key(fix["entity_type"], fix["entity_id"])
            self._records_by_key.setdefault(generic_key, fix)

    def get_capabilities(self) -> list[CapabilityDefinition]:
        return [
            CapabilityDefinition(
                name="get_workspace_summary",
                description="Get SaaS workspace details, status, and members count.",
                entity_type="workspace",
                parameters_schema={"type": "object", "properties": {"entity_id": {"type": "string"}}, "required": ["entity_id"]},
            ),
            CapabilityDefinition(
                name="list_customer_workspaces",
                description="List all workspaces belonging to the verified customer.",
                entity_type="workspace",
                parameters_schema={"type": "object", "properties": {}},
            ),
            CapabilityDefinition(
                name="get_subscription_summary",
                description="Get customer SaaS subscription tier and renewal date.",
                entity_type="subscription",
                parameters_schema={"type": "object", "properties": {"entity_id": {"type": "string"}}, "required": ["entity_id"]},
            ),
            CapabilityDefinition(
                name="get_api_quota_summary",
                description="Get API rate limit status and monthly quota consumption.",
                entity_type="api_quota",
                parameters_schema={"type": "object", "properties": {"entity_id": {"type": "string"}}, "required": ["entity_id"]},
            ),
        ]

    async def get_entity_summary(
        self, entity_type: str, entity_id: str, customer_id: str
    ) -> EntityRecord:
        scoped_key = self._build_key(entity_type, entity_id, customer_id)
        generic_key = self._build_key(entity_type, entity_id)
        rec = self._records_by_key.get(scoped_key) or self._records_by_key.get(generic_key)
        if not rec:
            raise EntityNotFoundError(f"Entity '{entity_id}' of type '{entity_type}' not found.")
        if rec["customer_id"] != customer_id:
            raise EntityNotFoundError(f"Entity '{entity_id}' does not belong to requesting customer.")

        return EntityRecord(
            entity_type=rec["entity_type"],
            entity_id=rec["entity_id"],
            customer_id=rec["customer_id"],
            title=rec["title"],
            summary_fields=rec["summary_fields"],
            raw_payload=rec["raw_payload"],
        )

    async def list_customer_entities(
        self, entity_type: str, customer_id: str
    ) -> list[EntityRecord]:
        norm_type = entity_type.strip().lower()
        results: list[EntityRecord] = []
        for rec in self._records_by_key.values():
            if rec["entity_type"] == norm_type and rec["customer_id"] == customer_id:
                results.append(
                    EntityRecord(
                        entity_type=rec["entity_type"],
                        entity_id=rec["entity_id"],
                        customer_id=rec["customer_id"],
                        title=rec["title"],
                        summary_fields=rec["summary_fields"],
                        raw_payload=rec["raw_payload"],
                    )
                )
        return results

