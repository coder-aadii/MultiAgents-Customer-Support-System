from __future__ import annotations

from typing import Any

import structlog

from app.connectors.base import BaseEnterpriseConnector
from app.gateway.base import (
    BaseConnector,
    CapabilityDefinition,
    ConnectorError,
    EntityNotFoundError,
    EntityRecord,
)

logger = structlog.get_logger()


class EnterpriseConnectorGatewayAdapter(BaseConnector):
    """Architectural bridge adapting BaseEnterpriseConnector into ToolGateway's BaseConnector.

    Enables the AI assistant and ToolGateway pipeline to transparently query
    real enterprise REST, SQL, and GraphQL connectors while producing standardized
    EntityRecord instances for TurnEvidenceRegistry, EvidenceRecord persistence,
    and deterministic fact verification.
    """

    def __init__(
        self,
        enterprise_connector: BaseEnterpriseConnector,
        entity_type: str = "order",
    ) -> None:
        self.enterprise_connector = enterprise_connector
        self.entity_type = entity_type.strip().lower()

    @property
    def connector_name(self) -> str:
        return f"adapter_{self.enterprise_connector.connector_name}"

    @property
    def supported_entity_types(self) -> list[str]:
        return [self.entity_type]

    def get_capabilities(self) -> list[CapabilityDefinition]:
        return [
            CapabilityDefinition(
                name=f"get_{self.entity_type}_summary",
                description=f"Retrieve summary and tracking for {self.entity_type} via enterprise {self.enterprise_connector.connector_name}",
                entity_type=self.entity_type,
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "entity_id": {"type": "string", "description": f"Unique {self.entity_type} ID"}
                    },
                    "required": ["entity_id"],
                },
                requires_ial=2,
            ),
            CapabilityDefinition(
                name=f"list_customer_{self.entity_type}s",
                description=f"List recent {self.entity_type} records for the verified customer",
                entity_type=self.entity_type,
                parameters_schema={"type": "object", "properties": {}},
                requires_ial=2,
            ),
        ]

    def _normalize_entity_dict(
        self, data: dict[str, Any], entity_id: str, customer_id: str
    ) -> EntityRecord:
        """Converts raw enterprise connector dict into normalized EntityRecord."""
        oid = str(data.get("order_id") or data.get("id") or entity_id)
        short_id = oid[:8] if len(oid) >= 8 else oid

        # Summary fields extraction
        summary_fields: dict[str, Any] = {}
        for k, v in data.items():
            if v is not None:
                summary_fields[k] = v

        # Normalize common field aliases
        if "status" not in summary_fields and "order_status" in summary_fields:
            summary_fields["status"] = summary_fields["order_status"]
        if "carrier" not in summary_fields:
            summary_fields["carrier"] = "Enterprise Express"

        return EntityRecord(
            entity_type=self.entity_type,
            entity_id=oid,
            customer_id=customer_id,
            title=f"Order #{short_id}",
            summary_fields=summary_fields,
            raw_payload=data,
        )

    async def get_entity_summary(
        self, entity_type: str, entity_id: str, customer_id: str
    ) -> EntityRecord:
        # Map to enterprise operation
        op_name = f"get_{self.entity_type}_details"
        if op_name not in self.enterprise_connector.supported_operations:
            op_name = "get_order_details"

        result = await self.enterprise_connector.execute_query(
            operation=op_name,
            verified_customer_id=customer_id,
            parameters={"order_id": entity_id},
        )

        if not result.success:
            if result.error_code == "NOT_FOUND":
                raise EntityNotFoundError(
                    f"Entity '{entity_type}:{entity_id}' not found for customer '{customer_id}'"
                )
            raise ConnectorError(
                result.error_message or f"Enterprise connector '{self.enterprise_connector.connector_name}' error"
            )

        if not result.data or not isinstance(result.data, dict):
            raise EntityNotFoundError(f"Entity '{entity_type}:{entity_id}' not found")

        return self._normalize_entity_dict(result.data, entity_id, customer_id)

    async def list_customer_entities(
        self, entity_type: str, customer_id: str
    ) -> list[EntityRecord]:
        op_name = f"get_{self.entity_type}s"
        if op_name not in self.enterprise_connector.supported_operations:
            op_name = "get_orders"

        result = await self.enterprise_connector.execute_query(
            operation=op_name,
            verified_customer_id=customer_id,
            parameters={},
        )

        if not result.success:
            raise ConnectorError(
                result.error_message or f"Enterprise connector '{self.enterprise_connector.connector_name}' error"
            )

        raw_items = result.data or []
        if isinstance(raw_items, dict):
            # If wrapped in parent object (e.g. GraphQL customer: { orders: [...] })
            for v in raw_items.values():
                if isinstance(v, list):
                    raw_items = v
                    break
                elif isinstance(v, dict):
                    for sub_v in v.values():
                        if isinstance(sub_v, list):
                            raw_items = sub_v
                            break

        if not isinstance(raw_items, list):
            return []

        records: list[EntityRecord] = []
        for item in raw_items:
            if isinstance(item, dict):
                eid = str(item.get("order_id") or item.get("id") or "unknown")
                records.append(self._normalize_entity_dict(item, eid, customer_id))

        return records

