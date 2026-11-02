from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.base import (
    BaseEnterpriseConnector,
    ConnectorAuthError,
    ConnectorResult,
)
from app.models.connector_audit_log import ConnectorAuditLog

if TYPE_CHECKING:
    from app.models.visitor_session import VisitorSession

logger = structlog.get_logger()


class ConnectorRegistry:
    """Central registry and lifecycle manager for enterprise connectors.

    Enforces:
    - Zero-trust customer ID resolution from VisitorSession.
    - Automated telemetry recording in connector_audit_logs.
    - Fallback and circuit-breaker monitoring.
    """

    def __init__(self, connectors: list[BaseEnterpriseConnector] | None = None) -> None:
        self._connectors: dict[str, BaseEnterpriseConnector] = {}
        if connectors:
            for c in connectors:
                self.register(c)

    def register(self, connector: BaseEnterpriseConnector) -> None:
        """Registers an enterprise connector instance."""
        self._connectors[connector.connector_name] = connector
        logger.info(
            "Registered enterprise connector",
            connector=connector.connector_name,
            operations=connector.supported_operations,
        )

    def get_connector(self, name: str) -> BaseEnterpriseConnector | None:
        return self._connectors.get(name)

    def list_connectors(self) -> list[str]:
        return list(self._connectors.keys())

    async def load_connectors_from_db(self, db: AsyncSession) -> int:
        """Loads and initializes active connectors configured in connector_definitions table."""
        from sqlalchemy import select
        from app.models.connector_definition import ConnectorDefinition
        from app.connectors.rest.connector import RestEnterpriseConnector

        result = await db.execute(
            select(ConnectorDefinition).where(ConnectorDefinition.is_enabled.is_(True))
        )
        definitions = result.scalars().all()
        count = 0
        for cd in definitions:
            if cd.connector_type == "rest" and cd.base_url:
                connector = RestEnterpriseConnector(
                    connector_name=cd.name,
                    base_url=cd.base_url,
                    auth_type=cd.auth_type,
                    secret_reference=cd.secret_reference,
                )
                self.register(connector)
                count += 1
        return count

    async def execute_and_audit(
        self,
        connector_name: str,
        operation: str,
        session: VisitorSession,
        parameters: dict[str, Any] | None = None,
        timeout_seconds: float = 5.0,
        db: AsyncSession | None = None,
    ) -> ConnectorResult:
        """Executes connector query and records non-sensitive operational telemetry."""
        connector = self.get_connector(connector_name)
        if not connector:
            return ConnectorResult(
                success=False,
                connector_name=connector_name,
                operation=operation,
                error_code="CONNECTOR_NOT_FOUND",
                error_message=f"Connector '{connector_name}' is not registered",
                execution_time_ms=0,
            )

        # Invariant: Must have verified customer identity (IAL >= 2)
        ial = getattr(session, "identity_assurance_level", 0)
        customer_id = getattr(session, "verified_customer_id", None)
        if ial < 2 or not customer_id:
            return ConnectorResult(
                success=False,
                connector_name=connector_name,
                operation=operation,
                error_code="AUTHORIZATION_REQUIRED",
                error_message="Insufficient identity assurance level (IAL >= 2 required)",
                execution_time_ms=0,
            )

        # Execute query
        result = await connector.execute_query(
            operation=operation,
            verified_customer_id=customer_id,
            parameters=parameters,
            timeout_seconds=timeout_seconds,
        )

        # Audit telemetry
        if db is not None:
            try:
                status_str = "success" if result.success else (result.error_code.lower() if result.error_code else "error")
                audit_entry = ConnectorAuditLog(
                    id=uuid.uuid4(),
                    connector_name=connector_name,
                    conversation_id=session.conversation_id,
                    customer_id=customer_id,
                    operation=operation,
                    execution_time_ms=result.execution_time_ms,
                    status=status_str[:32],
                    error_code=result.error_code[:64] if result.error_code else None,
                )
                db.add(audit_entry)
                await db.commit()
            except Exception as e:
                logger.error("Failed to write connector audit log", error=str(e))

        return result


default_connector_registry = ConnectorRegistry()

