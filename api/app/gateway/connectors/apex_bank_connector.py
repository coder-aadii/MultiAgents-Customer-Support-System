"""Deterministic Apex Global Bank connector for Fintech / Banking entities (accounts, transactions, wire transfers)."""

from __future__ import annotations

from typing import Any

from app.gateway.base import (
    BaseConnector,
    CapabilityDefinition,
    EntityNotFoundError,
    EntityRecord,
)


class ApexBankConnector(BaseConnector):
    """Connector representing a commercial and retail banking backend (Apex Global Bank).

    Supplies verified customer fixtures for bank accounts, posted transactions, and wire transfers.
    Enforces strict customer ownership and IDOR/BOLA isolation.
    """

    _NAME = "apex_bank"
    _SUPPORTED_TYPES = ["account", "transaction", "wire_transfer"]

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
            # Elena Rostova (cust_bank_401)
            {
                "entity_type": "account",
                "entity_id": "ACC-440192",
                "customer_id": "cust_bank_401",
                "title": "Commercial Checking Account #ACC-440192",
                "summary_fields": {
                    "account_id": "ACC-440192",
                    "account_type": "Commercial Checking",
                    "balance": 24850.00,
                    "currency": "USD",
                    "status": "active",
                    "holder": "Elena Rostova",
                },
                "raw_payload": {
                    "account_id": "ACC-440192",
                    "customer_id": "cust_bank_401",
                    "created_at": "2024-03-10T00:00:00Z",
                    "routing_number": "021000021",
                },
            },
            {
                "entity_type": "transaction",
                "entity_id": "TXN-55102",
                "customer_id": "cust_bank_401",
                "title": "Financial Transaction #TXN-55102",
                "summary_fields": {
                    "transaction_id": "TXN-55102",
                    "account_id": "ACC-440192",
                    "amount": 420.50,
                    "currency": "USD",
                    "merchant": "ElectroShop NYC",
                    "timestamp": "2026-11-10T14:32:00Z",
                    "status": "under_dispute",
                },
                "raw_payload": {
                    "transaction_id": "TXN-55102",
                    "customer_id": "cust_bank_401",
                    "card_last_four": "8821",
                },
            },
            {
                "entity_type": "wire_transfer",
                "entity_id": "WIRE-88129",
                "customer_id": "cust_bank_401",
                "title": "Wire Transfer #WIRE-88129",
                "summary_fields": {
                    "wire_id": "WIRE-88129",
                    "recipient": "Geneva Trading LLC",
                    "amount": 12000.00,
                    "currency": "USD",
                    "status": "completed",
                    "timestamp": "2026-11-08T09:15:00Z",
                },
                "raw_payload": {
                    "wire_id": "WIRE-88129",
                    "customer_id": "cust_bank_401",
                    "swift_bic": "GENTCH22",
                },
            },
            # Foreign records for BOLA isolation testing
            {
                "entity_type": "account",
                "entity_id": "ACC-999999",
                "customer_id": "cust_foreign_999",
                "title": "Foreign Retail Account",
                "summary_fields": {
                    "account_id": "ACC-999999",
                    "balance": 150.00,
                    "status": "active",
                },
                "raw_payload": {"customer_id": "cust_foreign_999"},
            },
        ]
        for fix in fixtures:
            key = self._build_key(fix["entity_type"], fix["entity_id"])
            self._records_by_key[key] = fix

    def get_capabilities(self) -> list[CapabilityDefinition]:
        return [
            CapabilityDefinition(
                name="get_account_summary",
                description="Get bank account balance, currency, and operational status.",
                entity_type="account",
                parameters_schema={"type": "object", "properties": {"entity_id": {"type": "string"}}, "required": ["entity_id"]},
            ),
            CapabilityDefinition(
                name="list_customer_accounts",
                description="List all bank accounts belonging to the verified customer.",
                entity_type="account",
                parameters_schema={"type": "object", "properties": {}},
            ),
            CapabilityDefinition(
                name="get_transaction_summary",
                description="Get transaction amount, merchant, and dispute status.",
                entity_type="transaction",
                parameters_schema={"type": "object", "properties": {"entity_id": {"type": "string"}}, "required": ["entity_id"]},
            ),
            CapabilityDefinition(
                name="get_wire_transfer_summary",
                description="Get SWIFT/Fedwire transfer details, recipient, and settlement status.",
                entity_type="wire_transfer",
                parameters_schema={"type": "object", "properties": {"entity_id": {"type": "string"}}, "required": ["entity_id"]},
            ),
        ]

    async def get_entity_summary(
        self, entity_type: str, entity_id: str, customer_id: str
    ) -> EntityRecord:
        key = self._build_key(entity_type, entity_id)
        rec = self._records_by_key.get(key)
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
