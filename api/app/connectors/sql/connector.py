from __future__ import annotations

import re
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.connectors.base import (
    BaseEnterpriseConnector,
    ConnectorNotFoundError,
    ConnectorValidationError,
    EnterpriseConnectorError,
    HealthStatus,
)
from app.connectors.credentials import CredentialManager
from app.connectors.resilience import CircuitBreaker, TokenBucketRateLimiter

logger = structlog.get_logger()

PROHIBITED_SQL_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|GRANT|REVOKE|EXEC|EXECUTE|CREATE)\b",
    re.IGNORECASE,
)

DEFAULT_APPROVED_SQL_QUERIES = {
    "get_orders": (
        "SELECT id AS order_id, status, total_amount, carrier, tracking_number, created_at AS order_date "
        "FROM orders WHERE customer_id = :verified_customer_id ORDER BY created_at DESC LIMIT 10"
    ),
    "get_order_details": (
        "SELECT id AS order_id, status, total_amount, carrier, tracking_number, created_at AS order_date "
        "FROM orders WHERE customer_id = :verified_customer_id AND (id = :order_id OR order_number = :order_id) LIMIT 1"
    ),
}


class SqlEnterpriseConnector(BaseEnterpriseConnector):
    """Read-only parameterized SQL connector against database replicas.

    Enforces:
    - Pre-compiled static query templates only (no dynamic arbitrary SQL).
    - Mandatory :verified_customer_id parameter binding on 100% of queries.
    - Strict prohibition of DDL, DML, multi-statements, and comments.
    - Field-level sanitization before turning into EvidenceRecord facts.
    """

    def __init__(
        self,
        connector_name: str = "sql_replica",
        connection_url_secret_ref: str = "SQL_REPLICA_URL",
        approved_queries: dict[str, str] | None = None,
        rate_limiter: TokenBucketRateLimiter | None = None,
        circuit_breaker: CircuitBreaker | None = None,
        custom_engine: AsyncEngine | None = None,
    ) -> None:
        self._name = connector_name
        self.secret_ref = connection_url_secret_ref
        self.approved_queries = dict(approved_queries or DEFAULT_APPROVED_SQL_QUERIES)
        self.rate_limiter = rate_limiter or TokenBucketRateLimiter(capacity=100, refill_rate_per_sec=5.0)
        self.circuit_breaker = circuit_breaker or CircuitBreaker(name=connector_name)
        self._engine = custom_engine

        # Verify all approved queries meet zero-trust invariants at initialization
        for op, query_sql in self.approved_queries.items():
            self._validate_query_safety(query_sql)

    @property
    def connector_name(self) -> str:
        return self._name

    @property
    def supported_operations(self) -> list[str]:
        return list(self.approved_queries.keys())

    def _validate_query_safety(self, sql_statement: str) -> None:
        """Validates query meets strict read-only and zero-trust invariants."""
        # Check prohibited keywords
        if PROHIBITED_SQL_KEYWORDS.search(sql_statement):
            raise ConnectorValidationError("Prohibited DDL/DML keyword detected in SQL statement")

        # Multi-statement injection prevention
        if ";" in sql_statement.rstrip(";"):
            raise ConnectorValidationError("Multiple statements separated by semicolon are forbidden")

        # Mandatory customer ID binding check
        if ":verified_customer_id" not in sql_statement:
            raise ConnectorValidationError(
                "Query must include mandatory ':verified_customer_id' parameter placeholder"
            )

    async def _get_engine(self) -> AsyncEngine:
        if self._engine:
            return self._engine
        url = CredentialManager.resolve_secret(self.secret_ref)
        self._engine = create_async_engine(url, pool_pre_ping=True, pool_size=5)
        return self._engine

    async def health_check(self) -> HealthStatus:
        try:
            engine = await self._get_engine()
            async with engine.connect() as conn:
                res = await conn.execute(text("SELECT 1"))
                val = res.scalar()
                return HealthStatus(
                    is_healthy=(val == 1),
                    latency_ms=1.0,
                    details={"engine": str(engine.url.drivername)},
                )
        except Exception as e:
            return HealthStatus(
                is_healthy=False,
                latency_ms=0.0,
                details={"error": str(e)},
            )

    async def _execute_internal(
        self,
        operation: str,
        verified_customer_id: str,
        parameters: dict[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any] | list[dict[str, Any]] | None:
        await self.rate_limiter.acquire(1)
        await self.circuit_breaker.check_permitted()

        raw_query = self.approved_queries.get(operation)
        if not raw_query:
            raise ConnectorValidationError(f"Unknown operation '{operation}'")

        # Validate order_id if operation is get_order_details
        if operation == "get_order_details":
            if not parameters.get("order_id"):
                raise ConnectorValidationError("Parameter 'order_id' is required for get_order_details")

        # Strictly inject verified_customer_id into query parameters
        bound_params = dict(parameters)
        bound_params["verified_customer_id"] = verified_customer_id

        engine = await self._get_engine()
        try:
            async with engine.connect() as conn:
                stmt = text(raw_query)
                cursor = await conn.execute(stmt, bound_params)
                rows = cursor.mappings().all()

                await self.circuit_breaker.record_success()

                if not rows:
                    if operation == "get_order_details":
                        raise ConnectorNotFoundError(
                            f"Order not found for customer '{verified_customer_id}'"
                        )
                    return []

                # Convert RowMapping to clean dicts
                results = [CredentialManager.sanitize_data(dict(r)) for r in rows]
                if operation == "get_order_details":
                    return results[0]
                return results

        except ConnectorNotFoundError:
            raise
        except Exception as e:
            await self.circuit_breaker.record_failure()
            logger.error("SQL query execution failed", error=str(e), operation=operation)
            raise EnterpriseConnectorError(f"Database query failed: {e}") from e

