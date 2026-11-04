from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

import structlog

from app.config import settings
from app.gateway.base import (
    CapabilityDefinition,
    ConnectorError,
    EntityNotFoundError,
    EntityRecord,
)
from app.gateway.connectors.mock_connector import MockBusinessConnector

logger = structlog.get_logger()


class OlistECommerceConnector(MockBusinessConnector):
    """Real E-Commerce data connector backed by Brazilian E-Commerce dataset in SQLite.

    Provides verified customer-scoped read access to:
    - 99,441 real customer records
    - 99,441 real orders
    - 112,650 order items
    - 103,886 order payment records

    Preserves full backward compatibility with MockBusinessConnector test fixtures.
    Enforces strict zero-trust customer data isolation (BOLA/IDOR prevention).
    """

    _NAME = "olist_ecommerce"
    _SUPPORTED_TYPES = ["order", "shipment", "refund", "customer"]

    def __init__(self, db_path: str | Path | None = None) -> None:
        super().__init__()
        self.db_path = self._resolve_db_path(db_path)
        logger.info("Initialized OlistECommerceConnector", db_path=str(self.db_path))

    @property
    def connector_name(self) -> str:
        return self._NAME

    @property
    def supported_entity_types(self) -> list[str]:
        return list(self._SUPPORTED_TYPES)

    def _resolve_db_path(self, explicit_path: str | Path | None = None) -> Path:
        """Resolves SQLite database path across Docker container, host workspace, or test environments."""
        candidates = [
            explicit_path,
            getattr(settings, "ECOMMERCE_DB_PATH", None),
            "/app/data/ecommerce_store.db",
            "api/data/ecommerce_store.db",
            Path(__file__).parent.parent.parent.parent / "data" / "ecommerce_store.db",
        ]
        for c in candidates:
            if c:
                p = Path(c).resolve()
                if p.exists() and p.is_file():
                    return p

        # Fallback to default expected path even if pending creation
        return Path("/app/data/ecommerce_store.db")

    def _get_connection(self) -> sqlite3.Connection:
        if not self.db_path.exists():
            raise ConnectorError(f"E-commerce database not found at {self.db_path}")
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def get_capabilities(self) -> list[CapabilityDefinition]:
        return [
            CapabilityDefinition(
                name="get_order_summary",
                description="Retrieve order status, dates, tracking number, and financial summary for an order",
                entity_type="order",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "entity_id": {
                            "type": "string",
                            "description": "Order ID (e.g. ORD-1001 or 32-char hex ID)",
                        }
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
                        "entity_id": {
                            "type": "string",
                            "description": "Tracking number (e.g. TRK-8801 or BR-XXXXXXXX)",
                        }
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
        norm_type = entity_type.strip().lower()

        # 1. Check if this is a seeded mock fixture (e.g. ORD-1001, TRK-8801, REF-5001)
        mock_key = self._build_key(norm_type, entity_id)
        if (
            norm_type == "refund"
            or mock_key in self._records_by_key
            or customer_id.startswith("cust_verified_")
            or customer_id.startswith("cust_other_")
        ):
            try:
                return await super().get_entity_summary(norm_type, entity_id, customer_id)
            except EntityNotFoundError:
                # If it's explicitly a mock customer/entity, fail fast with BOLA protection
                if customer_id.startswith(("cust_verified_", "cust_other_")) or entity_id.upper().startswith(("ORD-100", "TRK-880", "REF-")):
                    raise

        # 2. Query real SQLite dataset for orders
        if norm_type == "order":
            clean_id = entity_id.strip()
            if clean_id.upper().startswith("ORD-"):
                clean_id = clean_id[4:]
            elif clean_id.startswith("#"):
                clean_id = clean_id[1:]

            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                # Query order details + total payment + items count
                query = """
                    SELECT o.order_id, o.customer_id, o.order_status, o.purchase_timestamp, 
                           o.delivered_customer_date, o.estimated_delivery_date,
                           c.customer_unique_id, c.city, c.state,
                           COALESCE(SUM(p.payment_value), 0.0) as total_amount,
                           COUNT(DISTINCT i.item_id) as items_count
                    FROM orders o
                    JOIN customers c ON o.customer_id = c.customer_id
                    LEFT JOIN order_payments p ON o.order_id = p.order_id
                    LEFT JOIN order_items i ON o.order_id = i.order_id
                    WHERE (o.order_id = ? OR o.order_id LIKE ?)
                    GROUP BY o.order_id
                    LIMIT 1
                """
                cursor.execute(query, (clean_id.lower(), f"{clean_id.lower()}%"))
                row = cursor.fetchone()

                if not row:
                    raise EntityNotFoundError(f"Entity 'order:{entity_id}' not found")

                # Invariant: Strict BOLA check.
                # Must belong to requesting customer_id or linked customer_unique_id
                cursor.execute(
                    "SELECT customer_unique_id FROM customers WHERE customer_id = ? OR email = ? LIMIT 1",
                    (customer_id, customer_id),
                )
                cust_row = cursor.fetchone()
                req_unique_id = cust_row["customer_unique_id"] if cust_row else customer_id

                order_cust_id = row["customer_id"]
                order_unique_id = row["customer_unique_id"]

                if customer_id not in (order_cust_id, order_unique_id) and req_unique_id != order_unique_id:
                    logger.warning(
                        "BOLA access violation rejected",
                        customer_id=customer_id,
                        order_id=row["order_id"],
                        order_cust_id=order_cust_id,
                    )
                    raise EntityNotFoundError(f"Entity 'order:{entity_id}' not found")

                oid = row["order_id"]
                status = row["order_status"]
                short_id = oid[:8]
                tracking_num = f"BR-{short_id.upper()}"
                delivered_at = str(row["delivered_customer_date"])[:10] if row["delivered_customer_date"] else None
                estimated = str(row["estimated_delivery_date"])[:10] if row["estimated_delivery_date"] else None

                return EntityRecord(
                    entity_type="order",
                    entity_id=oid,
                    customer_id=customer_id,
                    title=f"Order #{short_id}",
                    summary_fields={
                        "status": status,
                        "total_amount": round(float(row["total_amount"]), 2),
                        "currency": "USD",
                        "items_count": int(row["items_count"]),
                        "carrier": "Correios Express",
                        "order_date": str(row["purchase_timestamp"])[:10],
                        "delivered_at": delivered_at,
                        "estimated_delivery": estimated,
                        "tracking_number": tracking_num,
                    },
                    raw_payload={
                        "order_id": oid,
                        "customer_id": order_cust_id,
                        "city": row["city"],
                        "state": row["state"],
                    },
                )
            finally:
                conn.close()

        # 3. Query real SQLite dataset for shipments
        if norm_type == "shipment":
            tracking_id = entity_id.strip().upper()
            order_prefix = tracking_id[3:].lower() if tracking_id.startswith("BR-") else tracking_id.lower()

            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT o.order_id, o.customer_id, o.order_status, 
                           o.delivered_customer_date, o.estimated_delivery_date,
                           c.customer_unique_id
                    FROM orders o
                    JOIN customers c ON o.customer_id = c.customer_id
                    WHERE (o.order_id LIKE ?)
                    LIMIT 1
                    """,
                    (f"{order_prefix}%",),
                )
                row = cursor.fetchone()
                if not row:
                    raise EntityNotFoundError(f"Entity 'shipment:{entity_id}' not found")

                cursor.execute(
                    "SELECT customer_unique_id FROM customers WHERE customer_id = ? OR email = ? LIMIT 1",
                    (customer_id, customer_id),
                )
                cust_row = cursor.fetchone()
                req_unique_id = cust_row["customer_unique_id"] if cust_row else customer_id

                if customer_id not in (row["customer_id"], row["customer_unique_id"]) and req_unique_id != row["customer_unique_id"]:
                    raise EntityNotFoundError(f"Entity 'shipment:{entity_id}' not found")

                oid = row["order_id"]
                short_id = oid[:8]
                delivered_at = str(row["delivered_customer_date"])[:10] if row["delivered_customer_date"] else None
                estimated = str(row["estimated_delivery_date"])[:10] if row["estimated_delivery_date"] else None

                return EntityRecord(
                    entity_type="shipment",
                    entity_id=tracking_id,
                    customer_id=customer_id,
                    title=f"Shipment #{tracking_id}",
                    summary_fields={
                        "status": row["order_status"],
                        "carrier": "Correios Express",
                        "order_id": f"ORD-{short_id}",
                        "delivered_at": delivered_at,
                        "estimated_delivery": estimated,
                    },
                    raw_payload={"order_id": oid, "customer_id": row["customer_id"]},
                )
            finally:
                conn.close()

        # 4. Query real SQLite dataset for customers
        if norm_type == "customer":
            clean_id = entity_id.strip()
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT customer_id, customer_unique_id, email, city, state FROM customers WHERE customer_id = ? OR email = ? OR customer_unique_id = ? LIMIT 1",
                    (clean_id, clean_id, clean_id),
                )
                row = cursor.fetchone()
                if not row:
                    raise EntityNotFoundError(f"Entity 'customer:{entity_id}' not found")

                cid = row["customer_id"]
                c_email = row["email"]
                unique_id = row["customer_unique_id"]
                if customer_id not in (cid, c_email, unique_id):
                    logger.warning(
                        "BOLA access violation rejected for customer lookup",
                        customer_id=customer_id,
                        target_id=clean_id,
                    )
                    raise EntityNotFoundError(f"Entity 'customer:{entity_id}' not found")

                return EntityRecord(
                    entity_type="customer",
                    entity_id=cid,
                    customer_id=customer_id,
                    title="Customer Profile",
                    summary_fields={
                        "customer_id": cid,
                        "customer_unique_id": unique_id,
                        "email": c_email,
                        "city": row["city"],
                        "state": row["state"],
                        "status": "active",
                    },
                    raw_payload={
                        "customer_id": cid,
                        "customer_unique_id": unique_id,
                        "email": c_email,
                        "city": row["city"],
                        "state": row["state"],
                    },
                )
            finally:
                conn.close()

        raise EntityNotFoundError(f"Entity '{entity_type}:{entity_id}' not found")

    async def list_customer_entities(
        self, entity_type: str, customer_id: str
    ) -> list[EntityRecord]:
        norm_type = entity_type.strip().lower()

        # 1. Check if customer entity requested
        if norm_type == "customer":
            rec = await self.get_entity_summary("customer", customer_id, customer_id)
            return [rec]

        # 1. Check if mock customer
        if customer_id.startswith(("cust_verified_", "cust_other_")):
            return await super().list_customer_entities(norm_type, customer_id)

        # 2. Query real SQLite dataset for customer's orders
        if norm_type == "order":
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                query = """
                    SELECT o.order_id, o.customer_id, o.order_status, o.purchase_timestamp, 
                           o.delivered_customer_date, o.estimated_delivery_date,
                           COALESCE(SUM(p.payment_value), 0.0) as total_amount,
                           COUNT(DISTINCT i.item_id) as items_count
                    FROM orders o
                    JOIN customers c ON o.customer_id = c.customer_id
                    LEFT JOIN order_payments p ON o.order_id = p.order_id
                    LEFT JOIN order_items i ON o.order_id = i.order_id
                    WHERE c.customer_unique_id = (
                        SELECT customer_unique_id FROM customers WHERE customer_id = ? OR email = ? LIMIT 1
                    ) OR o.customer_id = ?
                    GROUP BY o.order_id
                    ORDER BY o.purchase_timestamp DESC
                    LIMIT 10
                """
                cursor.execute(query, (customer_id, customer_id, customer_id))
                rows = cursor.fetchall()

                results: list[EntityRecord] = []
                for row in rows:
                    oid = row["order_id"]
                    short_id = oid[:8]
                    results.append(
                        EntityRecord(
                            entity_type="order",
                            entity_id=oid,
                            customer_id=customer_id,
                            title=f"Order #{short_id}",
                            summary_fields={
                                "status": row["order_status"],
                                "total_amount": round(float(row["total_amount"]), 2),
                                "currency": "USD",
                                "items_count": int(row["items_count"]),
                                "carrier": "Correios Express",
                                "order_date": str(row["purchase_timestamp"])[:10],
                                "delivered_at": str(row["delivered_customer_date"])[:10] if row["delivered_customer_date"] else None,
                                "estimated_delivery": str(row["estimated_delivery_date"])[:10] if row["estimated_delivery_date"] else None,
                                "tracking_number": f"BR-{short_id.upper()}",
                            },
                            raw_payload={"order_id": oid},
                        )
                    )
                return results
            finally:
                conn.close()

        return await super().list_customer_entities(norm_type, customer_id)

