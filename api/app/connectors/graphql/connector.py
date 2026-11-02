from __future__ import annotations

import re
from typing import Any

import httpx
import structlog

from app.connectors.base import (
    BaseEnterpriseConnector,
    ConnectorNotFoundError,
    ConnectorRateLimitError,
    ConnectorTimeoutError,
    ConnectorValidationError,
    EnterpriseConnectorError,
    HealthStatus,
)
from app.connectors.credentials import CredentialManager
from app.connectors.resilience import CircuitBreaker, TokenBucketRateLimiter

logger = structlog.get_logger()

INTROSPECTION_PATTERN = re.compile(r"(__schema|__type)", re.IGNORECASE)

DEFAULT_GRAPHQL_OPERATIONS = {
    "get_orders": """
        query GetCustomerOrders($customerId: ID!) {
            customer(id: $customerId) {
                orders {
                    id
                    orderNumber
                    status
                    totalAmount
                    carrier
                    trackingNumber
                    createdAt
                }
            }
        }
    """,
    "get_order_details": """
        query GetCustomerOrderDetail($customerId: ID!, $orderId: ID!) {
            customer(id: $customerId) {
                order(id: $orderId) {
                    id
                    orderNumber
                    status
                    totalAmount
                    carrier
                    trackingNumber
                    createdAt
                }
            }
        }
    """,
}


class GraphQLEnterpriseConnector(BaseEnterpriseConnector):
    """Production GraphQL enterprise data connector with strict AST/depth guardrails.

    Enforces:
    - Maximum query depth <= 3 to prevent nested amplification attacks.
    - Maximum field selection <= 20.
    - Prohibited introspection (__schema, __type).
    - Mandatory $customerId variable binding on all queries.
    """

    def __init__(
        self,
        connector_name: str = "graphql_enterprise",
        endpoint_url: str = "http://localhost:8000/graphql",
        secret_reference: str = "GRAPHQL_API_KEY",
        auth_type: str = "bearer",
        approved_operations: dict[str, str] | None = None,
        max_depth: int = 3,
        max_fields: int = 20,
        rate_limiter: TokenBucketRateLimiter | None = None,
        circuit_breaker: CircuitBreaker | None = None,
        custom_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._name = connector_name
        self.endpoint_url = endpoint_url
        self.secret_ref = secret_reference
        self.auth_type = auth_type.lower()
        self.approved_operations = dict(approved_operations or DEFAULT_GRAPHQL_OPERATIONS)
        self.max_depth = max_depth
        self.max_fields = max_fields
        self.rate_limiter = rate_limiter or TokenBucketRateLimiter(capacity=60, refill_rate_per_sec=2.0)
        self.circuit_breaker = circuit_breaker or CircuitBreaker(name=connector_name)
        self._client = custom_client

        # Validate operations on initialize
        for op, q in self.approved_operations.items():
            self._validate_graphql_query(q)

    @property
    def connector_name(self) -> str:
        return self._name

    @property
    def supported_operations(self) -> list[str]:
        return list(self.approved_operations.keys())

    def _validate_graphql_query(self, query: str) -> None:
        """Validates GraphQL query meets strict security guardrails."""
        # 1. Prohibit introspection
        if INTROSPECTION_PATTERN.search(query):
            raise ConnectorValidationError("GraphQL introspection queries are prohibited")

        # 2. Maximum depth check (bracket depth tracking ignoring string literals)
        depth = 0
        max_seen = 0
        in_string = False
        escape = False
        for char in query:
            if char == '"' and not escape:
                in_string = not in_string
            elif char == '\\' and in_string:
                escape = not escape
                continue
            elif not in_string:
                if char == "{":
                    depth += 1
                    max_seen = max(max_seen, depth)
                elif char == "}":
                    depth -= 1
            escape = False
        if max_seen > self.max_depth + 1:  # +1 for outer query wrapper
            raise ConnectorValidationError(
                f"GraphQL query depth {max_seen - 1} exceeds maximum permitted depth {self.max_depth}"
            )

        # 3. Maximum field selection count estimate
        tokens = [t.strip() for t in re.split(r"[\s,\{\}\(\)]+", query) if t.strip()]
        # Ignore graphql keywords
        fields = [t for t in tokens if t not in ("query", "mutation", "subscription", "ID", "String", "Int")]
        if len(fields) > self.max_fields + 5:
            raise ConnectorValidationError(
                f"GraphQL query fields count exceeds maximum limit of {self.max_fields}"
            )

        # 4. Mandatory $customerId variable definition
        if "$customerId" not in query:
            raise ConnectorValidationError(
                "GraphQL query must define and bind mandatory '$customerId' variable"
            )

    def _get_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.auth_type == "none":
            return headers

        secret = CredentialManager.resolve_secret(self.secret_ref)
        if self.auth_type == "bearer":
            headers["Authorization"] = f"Bearer {secret}"
        elif self.auth_type == "api_key":
            headers["X-Api-Key"] = secret
        return headers

    async def health_check(self) -> HealthStatus:
        headers = self._get_headers()
        client = self._client or httpx.AsyncClient(timeout=3.0)
        try:
            res = await client.post(
                self.endpoint_url,
                json={"query": "query { __typename }"},
                headers=headers,
            )
            is_ok = res.status_code == 200
            latency = res.elapsed.total_seconds() * 1000 if hasattr(res, "elapsed") else 5.0
            return HealthStatus(
                is_healthy=is_ok,
                latency_ms=latency,
                details={"status_code": res.status_code},
            )
        except Exception as e:
            return HealthStatus(
                is_healthy=False,
                latency_ms=0.0,
                details={"error": str(e)},
            )
        finally:
            if not self._client:
                await client.aclose()

    async def _execute_internal(
        self,
        operation: str,
        verified_customer_id: str,
        parameters: dict[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any] | list[dict[str, Any]] | None:
        await self.rate_limiter.acquire(1)
        await self.circuit_breaker.check_permitted()

        query_str = self.approved_operations.get(operation)
        if not query_str:
            raise ConnectorValidationError(f"Unknown operation '{operation}'")

        variables = dict(parameters)
        # Server-side mandatory customer ID injection
        variables["customerId"] = verified_customer_id

        if operation == "get_order_details" and not variables.get("orderId"):
            raise ConnectorValidationError("Parameter 'orderId' is required for get_order_details")

        payload = {"query": query_str, "variables": variables}
        headers = self._get_headers()

        client = self._client or httpx.AsyncClient(timeout=timeout_seconds)
        try:
            try:
                res = await client.post(self.endpoint_url, json=payload, headers=headers)
            except httpx.TimeoutException as e:
                await self.circuit_breaker.record_failure()
                raise ConnectorTimeoutError(f"GraphQL request timed out after {timeout_seconds}s") from e
            except httpx.RequestError as e:
                await self.circuit_breaker.record_failure()
                raise EnterpriseConnectorError(f"GraphQL network error: {e}") from e

            if res.status_code != 200:
                await self.circuit_breaker.record_failure()
                raise EnterpriseConnectorError(f"GraphQL upstream error (HTTP {res.status_code})")

            body = res.json()
            if "errors" in body and body["errors"]:
                err_msg = body["errors"][0].get("message", "GraphQL query error")
                if "not found" in err_msg.lower():
                    await self.circuit_breaker.record_success()
                    raise ConnectorNotFoundError(err_msg)
                await self.circuit_breaker.record_failure()
                raise EnterpriseConnectorError(f"GraphQL upstream error: {err_msg}")

            await self.circuit_breaker.record_success()
            data = body.get("data")
            return CredentialManager.sanitize_data(data)

        finally:
            if not self._client:
                await client.aclose()

