from __future__ import annotations

from typing import Any

from app.gateway.base import (
    BaseConnector,
    CapabilityDefinition,
    EntityNotFoundError,
    EntityRecord,
)


class MockBusinessConnector(BaseConnector):
    """Deterministic, domain-agnostic mock business connector for customer-specific records.

    Provides seeded fixtures for orders, shipments, and refunds.
    Guarantees strict customer ownership enforcement and BOLA prevention.
    """

    _NAME = "mock_business"
    _SUPPORTED_TYPES = ["order", "shipment", "refund", "customer"]

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

    def _build_key(self, entity_type: str, entity_id: str) -> str:
        return f"{entity_type.strip().lower()}:{entity_id.strip().upper()}"

    def _seed_default_fixtures(self) -> None:
        fixtures: list[dict[str, Any]] = [
            # Verified customer 123 fixtures
            {
                "entity_type": "order",
                "entity_id": "ORD-1001",
                "customer_id": "cust_verified_123",
                "title": "Order #ORD-1001",
                "summary_fields": {
                    "status": "delivered",
                    "total_amount": 129.99,
                    "currency": "USD",
                    "items_count": 2,
                    "items_summary": "Ergonomic Keyboard, Wireless Mouse",
                    "tracking_number": "TRK-8801",
                    "carrier": "FastShip Express",
                    "order_date": "2026-10-20",
                    "delivered_at": "2026-10-25",
                },
                "raw_payload": {
                    "order_id": "ORD-1001",
                    "customer_id": "cust_verified_123",
                    "line_items": [
                        {"sku": "KB-ERG-01", "name": "Ergonomic Keyboard", "price": 99.99, "qty": 1},
                        {"sku": "MS-WRL-02", "name": "Wireless Mouse", "price": 30.00, "qty": 1},
                    ],
                    "shipping_address": {
                        "street": "123 Main St",
                        "city": "Springfield",
                        "state": "IL",
                        "postal_code": "62701",
                    },
                },
            },
            {
                "entity_type": "order",
                "entity_id": "ORD-1002",
                "customer_id": "cust_verified_123",
                "title": "Order #ORD-1002",
                "summary_fields": {
                    "status": "in_transit",
                    "total_amount": 89.50,
                    "currency": "USD",
                    "items_count": 1,
                    "items_summary": "USB-C Docking Station",
                    "tracking_number": "TRK-8802",
                    "carrier": "GlobalFreight",
                    "order_date": "2026-10-28",
                    "estimated_delivery": "2026-11-05",
                },
                "raw_payload": {
                    "order_id": "ORD-1002",
                    "customer_id": "cust_verified_123",
                    "line_items": [
                        {"sku": "DCK-USBC-01", "name": "USB-C Docking Station", "price": 89.50, "qty": 1}
                    ],
                },
            },
            {
                "entity_type": "shipment",
                "entity_id": "TRK-8801",
                "customer_id": "cust_verified_123",
                "title": "Shipment #TRK-8801",
                "summary_fields": {
                    "status": "delivered",
                    "carrier": "FastShip Express",
                    "order_id": "ORD-1001",
                    "delivered_at": "2026-10-25",
                },
                "raw_payload": {
                    "tracking_number": "TRK-8801",
                    "events": [
                        {"time": "2026-10-21T08:00:00Z", "status": "shipped"},
                        {"time": "2026-10-25T14:30:00Z", "status": "delivered"},
                    ],
                },
            },
            {
                "entity_type": "shipment",
                "entity_id": "TRK-8802",
                "customer_id": "cust_verified_123",
                "title": "Shipment #TRK-8802",
                "summary_fields": {
                    "status": "in_transit",
                    "carrier": "GlobalFreight",
                    "order_id": "ORD-1002",
                    "estimated_delivery": "2026-11-05",
                },
                "raw_payload": {
                    "tracking_number": "TRK-8802",
                    "events": [{"time": "2026-10-29T10:00:00Z", "status": "in_transit"}],
                },
            },
            {
                "entity_type": "refund",
                "entity_id": "REF-5001",
                "customer_id": "cust_verified_123",
                "title": "Refund #REF-5001",
                "summary_fields": {
                    "status": "completed",
                    "amount": 25.00,
                    "currency": "USD",
                    "order_id": "ORD-1001",
                    "reason": "Damaged packaging rebate",
                    "processed_at": "2026-10-26",
                },
                "raw_payload": {
                    "refund_id": "REF-5001",
                    "order_id": "ORD-1001",
                    "amount": 25.00,
                    "method": "original_payment_method",
                },
            },
            {
                "entity_type": "customer",
                "entity_id": "cust_verified_123",
                "customer_id": "cust_verified_123",
                "title": "Customer Profile",
                "summary_fields": {
                    "customer_id": "cust_verified_123",
                    "email": "verified@example.com",
                    "city": "Springfield",
                    "state": "IL",
                    "status": "active",
                },
                "raw_payload": {
                    "customer_id": "cust_verified_123",
                    "email": "verified@example.com",
                    "city": "Springfield",
                    "state": "IL",
                },
            },
            # Isolated other customer fixtures (for BOLA and cross-customer isolation tests)
            {
                "entity_type": "order",
                "entity_id": "ORD-2001",
                "customer_id": "cust_other_456",
                "title": "Order #ORD-2001",
                "summary_fields": {
                    "status": "processing",
                    "total_amount": 49.99,
                    "currency": "USD",
                    "items_count": 1,
                    "items_summary": "Headphone Stand",
                    "order_date": "2026-10-31",
                },
                "raw_payload": {
                    "order_id": "ORD-2001",
                    "customer_id": "cust_other_456",
                },
            },
            {
                "entity_type": "customer",
                "entity_id": "cust_other_456",
                "customer_id": "cust_other_456",
                "title": "Customer Profile",
                "summary_fields": {
                    "customer_id": "cust_other_456",
                    "email": "other@example.com",
                    "city": "Metropolis",
                    "state": "NY",
                    "status": "active",
                },
                "raw_payload": {
                    "customer_id": "cust_other_456",
                    "email": "other@example.com",
                    "city": "Metropolis",
                    "state": "NY",
                },
            },
        ]

        for fix in fixtures:
            key = self._build_key(fix["entity_type"], fix["entity_id"])
            self._records_by_key[key] = fix

    def get_capabilities(self) -> list[CapabilityDefinition]:
        return [
            CapabilityDefinition(
                name="get_order_summary",
                description="Retrieve order status, dates, tracking number, and summary for an order",
                entity_type="order",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "entity_id": {"type": "string", "description": "Order number, e.g. ORD-1001"}
                    },
                    "required": ["entity_id"],
                },
                requires_ial=2,
            ),
            CapabilityDefinition(
                name="list_customer_orders",
                description="List recent orders placed by the verified customer",
                entity_type="order",
                parameters_schema={"type": "object", "properties": {}},
                requires_ial=2,
            ),
            CapabilityDefinition(
                name="get_shipment_status",
                description="Retrieve delivery tracking details for a shipment",
                entity_type="shipment",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "entity_id": {"type": "string", "description": "Tracking number, e.g. TRK-8801"}
                    },
                    "required": ["entity_id"],
                },
                requires_ial=2,
            ),
            CapabilityDefinition(
                name="get_refund_status",
                description="Retrieve refund processing status and details",
                entity_type="refund",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "entity_id": {"type": "string", "description": "Refund ID, e.g. REF-5001"}
                    },
                    "required": ["entity_id"],
                },
                requires_ial=2,
            ),
            CapabilityDefinition(
                name="get_customer_profile",
                description="Retrieve customer profile and account details for the verified customer",
                entity_type="customer",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "entity_id": {"type": "string", "description": "Customer ID or email"}
                    },
                    "required": ["entity_id"],
                },
                requires_ial=2,
            ),
        ]

    async def get_entity_summary(
        self, entity_type: str, entity_id: str, customer_id: str
    ) -> EntityRecord:
        key = self._build_key(entity_type, entity_id)
        record = self._records_by_key.get(key)
        if not record:
            for r in self._records_by_key.values():
                if r["entity_type"].lower() == entity_type.strip().lower() and r["entity_id"].lower() == entity_id.strip().lower():
                    record = r
                    break

        # Invariant: If entity does not exist OR belongs to another customer, raise EntityNotFoundError.
        # This completely prevents customer ID and order ID enumeration (BOLA/IDOR).
        if not record or record["customer_id"].lower() != customer_id.strip().lower():
            raise EntityNotFoundError(
                f"Entity '{entity_type}:{entity_id}' not found"
            )

        return EntityRecord(
            entity_type=record["entity_type"],
            entity_id=record["entity_id"],
            customer_id=record["customer_id"],
            title=record["title"],
            summary_fields=dict(record["summary_fields"]),
            raw_payload=dict(record.get("raw_payload", {})),
        )

    async def list_customer_entities(
        self, entity_type: str, customer_id: str
    ) -> list[EntityRecord]:
        norm_type = entity_type.strip().lower()
        results: list[EntityRecord] = []

        for record in self._records_by_key.values():
            if record["entity_type"].lower() == norm_type and record["customer_id"] == customer_id:
                results.append(
                    EntityRecord(
                        entity_type=record["entity_type"],
                        entity_id=record["entity_id"],
                        customer_id=record["customer_id"],
                        title=record["title"],
                        summary_fields=dict(record["summary_fields"]),
                        raw_payload=dict(record.get("raw_payload", {})),
                    )
                )

        return results

