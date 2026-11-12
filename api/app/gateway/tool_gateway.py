from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.gateway.base import (
    BaseConnector,
    CapabilityDefinition,
    ConnectorError,
    EntityNotFoundError,
    EntityRecord,
    GatewayError,
    ToolAuthorizationError,
    ToolTimeoutError,
    ToolValidationError,
)
from app.gateway.connectors.apex_bank_connector import ApexBankConnector
from app.gateway.connectors.cloudpulse_connector import CloudPulseSaaSConnector
from app.gateway.connectors.medicare_connector import MediCareConnector
from app.gateway.connectors.mock_connector import MockBusinessConnector
from app.gateway.connectors.olist_connector import OlistECommerceConnector
from app.models.audit_event import ActorType, AuditEvent

if TYPE_CHECKING:
    from app.models.visitor_session import VisitorSession
    from app.schemas.evidence import TurnEvidenceRegistry

logger = structlog.get_logger()


class ToolGateway:
    """Zero-Trust Tool Gateway governing external data connector access.

    Enforces:
    1. Zero-Trust Authorization:
       - Demands VisitorSession with IAL >= 2.
       - Rejects unverified visitors, claimed-only identities, expired tokens, and revoked sessions.
    2. Server-Side Customer ID Injection:
       - Extracts verified_customer_id directly from the authenticated server session.
       - Rejects any attempt by callers or models to specify, override, or spoof customer_id.
    3. Bounded Execution Timeouts:
       - Enforces strict asynchronous timeouts on connector invocations.
    4. Audit Logging:
       - Emits 'tool.execute' audit events recording operations without leaking private PII.
    5. Evidence Registry Integration:
       - Automatically normalizes retrieved entity records into EvidenceRecordCreate schemas.
    """

    def __init__(self, connectors: list[BaseConnector] | None = None) -> None:
        self._connectors: dict[str, BaseConnector] = {}
        self._entity_type_map: dict[str, BaseConnector] = {}

        if connectors is not None:
            for connector in connectors:
                self.register_connector(connector)
        elif getattr(settings, "MCSS_USE_MOCK_CONNECTORS", False):
            # Explicit rollback or local development override
            self.register_connector(MockBusinessConnector())
        else:
            # Default to all enterprise connectors across retail, SaaS, banking, and telehealth
            self.register_connector(OlistECommerceConnector())
            self.register_connector(CloudPulseSaaSConnector())
            self.register_connector(ApexBankConnector())
            self.register_connector(MediCareConnector())

    def register_connector(self, connector: BaseConnector) -> None:
        """Registers a connector and maps its supported entity types."""
        self._connectors[connector.connector_name] = connector
        for et in connector.supported_entity_types:
            self._entity_type_map[et.strip().lower()] = connector
        logger.info(
            "Registered tool connector",
            connector=connector.connector_name,
            entity_types=connector.supported_entity_types,
        )

    def get_connector_for_entity(self, entity_type: str) -> BaseConnector:
        """Finds the registered connector for an entity type."""
        norm_type = entity_type.strip().lower()
        connector = self._entity_type_map.get(norm_type)
        if not connector:
            raise ConnectorError(
                f"No connector registered for entity type '{entity_type}'"
            )
        return connector

    def list_capabilities(self) -> list[CapabilityDefinition]:
        """Lists all capabilities exposed by all registered connectors."""
        capabilities: list[CapabilityDefinition] = []
        for conn in self._connectors.values():
            capabilities.extend(conn.get_capabilities())
        return capabilities

    def verify_session_authorization(
        self, session: VisitorSession, required_ial: int = 2
    ) -> None:
        """Asserts that the session possesses cryptographic customer verification (IAL >= 2).

        Raises ToolAuthorizationError if unauthorized.
        """
        # 1. Identity Assurance Level Check
        current_ial = getattr(session, "identity_assurance_level", 0)
        if current_ial < required_ial:
            raise ToolAuthorizationError(
                f"Insufficient identity assurance level: session has IAL {current_ial}, "
                f"but IAL {required_ial} (Cryptographically Verified) is required"
            )

        # 2. Verified Customer ID Presence Check
        verified_customer_id = getattr(session, "verified_customer_id", None)
        if not verified_customer_id or not verified_customer_id.strip():
            raise ToolAuthorizationError(
                "Session is missing a verified customer ID binding"
            )

        # 3. Expiration Check
        customer_token_expires_at = getattr(session, "customer_token_expires_at", None)
        if customer_token_expires_at is not None:
            now = datetime.now(UTC)
            # Ensure timezone-aware comparison
            if customer_token_expires_at.tzinfo is None:
                customer_token_expires_at = customer_token_expires_at.replace(tzinfo=UTC)
            if customer_token_expires_at < now:
                raise ToolAuthorizationError(
                    "Customer identity token has expired; session degraded to anonymous"
                )

        # 4. Revocation Check
        if getattr(session, "revoked_at", None) is not None:
            raise ToolAuthorizationError("Session has been revoked")

    async def execute_get_entity(
        self,
        session: VisitorSession,
        entity_type: str,
        entity_id: str,
        db: AsyncSession | None = None,
        registry: TurnEvidenceRegistry | None = None,
        timeout_seconds: float = 5.0,
    ) -> EntityRecord:
        """Executes a single entity lookup with verified customer ownership injection."""
        start_time = time.monotonic()
        clean_entity_id = entity_id.strip()

        # 1. Zero-trust authorization check
        self.verify_session_authorization(session, required_ial=2)
        verified_customer_id = session.verified_customer_id
        assert verified_customer_id is not None

        # 2. Resolve connector
        connector = self.get_connector_for_entity(entity_type)

        # 3. Execute with bounded timeout
        success = False
        error_name: str | None = None
        try:
            entity = await asyncio.wait_for(
                connector.get_entity_summary(
                    entity_type=entity_type,
                    entity_id=clean_entity_id,
                    customer_id=verified_customer_id,
                ),
                timeout=timeout_seconds,
            )
            success = True

            # 4. Optional TurnEvidenceRegistry registration & DB persistence
            if registry is not None:
                record = entity.to_evidence_record_create()
                registry.register_record(record)

            if db is not None and getattr(session, "conversation_id", None):
                from app.services.evidence_service import record_evidence
                await record_evidence(
                    db=db,
                    conversation_id=session.conversation_id,
                    evidence_key=f"{entity.entity_type}:{entity.entity_id}",
                    source_type="business_entity",
                    title=entity.title,
                    payload_json={
                        "entity_id": entity.entity_id,
                        "summary_fields": entity.summary_fields,
                        "raw": entity.raw_payload,
                    },
                    entity_type=entity.entity_type,
                    verified_customer_id=verified_customer_id,
                )

            return entity

        except TimeoutError:
            error_name = "ToolTimeoutError"
            raise ToolTimeoutError(
                f"Connector '{connector.connector_name}' timed out after {timeout_seconds}s "
                f"fetching {entity_type}:{clean_entity_id}"
            )
        except EntityNotFoundError:
            if entity_type.lower() == "customer" and clean_entity_id == verified_customer_id:
                cust_name = getattr(session, "customer_name", None)
                cust_email = getattr(session, "customer_email", None)
                if db is not None and getattr(session, "visitor_id", None):
                    from app.models.visitor import Visitor
                    v = await db.get(Visitor, session.visitor_id)
                    if v:
                        cust_name = cust_name or v.name
                        cust_email = cust_email or v.email
                cust_name = cust_name or clean_entity_id
                cust_email = cust_email or f"{clean_entity_id}@verified-customer.example.com"
                entity = EntityRecord(
                    entity_type="customer",
                    entity_id=clean_entity_id,
                    customer_id=clean_entity_id,
                    title=f"Verified Customer {cust_name}",
                    summary_fields={
                        "customer_id": clean_entity_id,
                        "name": cust_name,
                        "email": cust_email,
                        "status": "active",
                        "ial": getattr(session, "identity_assurance_level", 2),
                    },
                    raw_payload={
                        "customer_id": clean_entity_id,
                        "name": cust_name,
                        "email": cust_email,
                    },
                )
                success = True
                if registry is not None:
                    record = entity.to_evidence_record_create()
                    registry.register_record(record)
                return entity
            error_name = "EntityNotFoundError"
            raise
        except Exception as exc:
            error_name = exc.__class__.__name__
            if isinstance(exc, GatewayError):
                raise
            raise ConnectorError(
                f"Connector '{connector.connector_name}' failed: {exc}"
            ) from exc
        finally:
            elapsed_ms = int((time.monotonic() - start_time) * 1000)
            if db is not None:
                await self._record_audit_event(
                    db=db,
                    session=session,
                    operation="get_entity",
                    entity_type=entity_type,
                    entity_id=clean_entity_id,
                    customer_id=verified_customer_id,
                    success=success,
                    execution_time_ms=elapsed_ms,
                    error=error_name,
                )

    async def execute_list_entities(
        self,
        session: VisitorSession,
        entity_type: str,
        db: AsyncSession | None = None,
        registry: TurnEvidenceRegistry | None = None,
        timeout_seconds: float = 5.0,
    ) -> list[EntityRecord]:
        """Lists all entities of a given type belonging to the verified customer."""
        start_time = time.monotonic()

        # 1. Zero-trust authorization check
        self.verify_session_authorization(session, required_ial=2)
        verified_customer_id = session.verified_customer_id
        assert verified_customer_id is not None

        # 2. Resolve connector
        connector = self.get_connector_for_entity(entity_type)

        # 3. Execute with bounded timeout
        success = False
        error_name: str | None = None
        try:
            entities = await asyncio.wait_for(
                connector.list_customer_entities(
                    entity_type=entity_type,
                    customer_id=verified_customer_id,
                ),
                timeout=timeout_seconds,
            )
            success = True

            # 4. Optional TurnEvidenceRegistry registration
            if registry is not None:
                for entity in entities:
                    registry.register_record(entity.to_evidence_record_create())

            return entities

        except TimeoutError:
            error_name = "ToolTimeoutError"
            raise ToolTimeoutError(
                f"Connector '{connector.connector_name}' timed out after {timeout_seconds}s "
                f"listing {entity_type} for customer"
            )
        except Exception as exc:
            error_name = exc.__class__.__name__
            if isinstance(exc, GatewayError):
                raise
            raise ConnectorError(
                f"Connector '{connector.connector_name}' failed: {exc}"
            ) from exc
        finally:
            elapsed_ms = int((time.monotonic() - start_time) * 1000)
            if db is not None:
                await self._record_audit_event(
                    db=db,
                    session=session,
                    operation="list_entities",
                    entity_type=entity_type,
                    entity_id=None,
                    customer_id=verified_customer_id,
                    success=success,
                    execution_time_ms=elapsed_ms,
                    error=error_name,
                )

    async def execute_tool(
        self,
        session: VisitorSession,
        capability_name: str,
        parameters: dict[str, Any],
        db: AsyncSession | None = None,
        registry: TurnEvidenceRegistry | None = None,
        timeout_seconds: float = 5.0,
    ) -> Any:
        """Generic tool dispatcher by capability name.

        Validates that caller does not attempt to supply or override customer_id.
        """
        # Security check: caller cannot override or supply customer_id
        if "customer_id" in parameters:
            raise ToolValidationError(
                "Prohibited parameter: caller cannot supply or override customer_id"
            )

        match capability_name:
            case "get_order_summary":
                entity_id = parameters.get("entity_id")
                if not entity_id or not isinstance(entity_id, str):
                    raise ToolValidationError("Missing or invalid 'entity_id' parameter")
                return await self.execute_get_entity(
                    session=session,
                    entity_type="order",
                    entity_id=entity_id,
                    db=db,
                    registry=registry,
                    timeout_seconds=timeout_seconds,
                )

            case "list_customer_orders":
                return await self.execute_list_entities(
                    session=session,
                    entity_type="order",
                    db=db,
                    registry=registry,
                    timeout_seconds=timeout_seconds,
                )

            case "get_shipment_status":
                entity_id = parameters.get("entity_id")
                if not entity_id or not isinstance(entity_id, str):
                    raise ToolValidationError("Missing or invalid 'entity_id' parameter")
                return await self.execute_get_entity(
                    session=session,
                    entity_type="shipment",
                    entity_id=entity_id,
                    db=db,
                    registry=registry,
                    timeout_seconds=timeout_seconds,
                )

            case "get_refund_status":
                entity_id = parameters.get("entity_id")
                if not entity_id or not isinstance(entity_id, str):
                    raise ToolValidationError("Missing or invalid 'entity_id' parameter")
                return await self.execute_get_entity(
                    session=session,
                    entity_type="refund",
                    entity_id=entity_id,
                    db=db,
                    registry=registry,
                    timeout_seconds=timeout_seconds,
                )

            case _:
                raise ToolValidationError(f"Unknown capability '{capability_name}'")

    async def _record_audit_event(
        self,
        db: AsyncSession,
        session: VisitorSession,
        operation: str,
        entity_type: str,
        entity_id: str | None,
        customer_id: str,
        success: bool,
        execution_time_ms: int,
        error: str | None,
    ) -> None:
        """Records an immutable audit event for tool execution."""
        try:
            metadata: dict[str, Any] = {
                "operation": operation,
                "entity_type": entity_type,
                "customer_id": customer_id,
                "success": success,
                "execution_time_ms": execution_time_ms,
            }
            if entity_id:
                metadata["entity_id"] = entity_id
            if error:
                metadata["error"] = error

            audit = AuditEvent(
                actor_type=ActorType.visitor,
                actor_id=session.visitor_id,
                event_type="tool.execute",
                resource_type="business_entity",
                resource_id=session.conversation_id,
                metadata_json=metadata,
                created_at=datetime.now(UTC),
            )
            db.add(audit)
            await db.flush()
        except Exception as exc:
            # Audit recording must not mask connector results, but log error
            logger.warning("Failed to persist tool.execute audit event", error=str(exc))


default_tool_gateway = ToolGateway()

