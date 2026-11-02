from __future__ import annotations

import json
from typing import Any

import httpx
import structlog

from app.connectors.base import (
    BaseEnterpriseConnector,
    ConnectorAuthError,
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

DEFAULT_ALLOWED_ORDER_FIELDS = {
    "order_id",
    "id",
    "order_number",
    "customer_id",
    "status",
    "order_status",
    "total_amount",
    "total",
    "currency",
    "carrier",
    "tracking_number",
    "order_date",
    "created_at",
    "delivered_at",
    "estimated_delivery",
    "items_count",
    "items_summary",
    "line_items",
}


class RestEnterpriseConnector(BaseEnterpriseConnector):
    """Production-grade REST enterprise data connector.

    Enforces:
    - Zero-trust customer ID injection in all path/query parameters.
    - Token-bucket rate limiting and fail-closed circuit breaker.
    - Sensitive header scrubbing and field-level least-privilege filtering.
    """

    def __init__(
        self,
        connector_name: str = "rest_ecommerce",
        base_url: str = "http://localhost:8000",
        auth_type: str = "bearer",
        secret_reference: str = "REST_API_KEY",
        rate_limiter: TokenBucketRateLimiter | None = None,
        circuit_breaker: CircuitBreaker | None = None,
        allowed_fields: set[str] | None = None,
        custom_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._name = connector_name
        self.base_url = base_url.rstrip("/")
        self.auth_type = auth_type.lower()
        self.secret_reference = secret_reference
        self.rate_limiter = rate_limiter or TokenBucketRateLimiter(capacity=60, refill_rate_per_sec=2.0)
        self.circuit_breaker = circuit_breaker or CircuitBreaker(name=connector_name)
        self.allowed_fields = allowed_fields or DEFAULT_ALLOWED_ORDER_FIELDS
        self._client = custom_client
        self._internal_client: httpx.AsyncClient | None = None

    async def _get_client(self, timeout_seconds: float) -> httpx.AsyncClient:
        if self._client:
            return self._client
        if self._internal_client is None or self._internal_client.is_closed:
            self._internal_client = httpx.AsyncClient(
                timeout=timeout_seconds,
                limits=httpx.Limits(max_keepalive_connections=20, max_connections=50),
            )
        return self._internal_client

    async def aclose(self) -> None:
        if self._internal_client and not self._internal_client.is_closed:
            await self._internal_client.aclose()

    @property
    def connector_name(self) -> str:
        return self._name

    @property
    def supported_operations(self) -> list[str]:
        return ["get_orders", "get_order_details"]

    def _get_auth_headers(self) -> dict[str, str]:
        if self.auth_type == "none":
            return {}

        secret = CredentialManager.resolve_secret(self.secret_reference)
        if self.auth_type == "bearer":
            return {"Authorization": f"Bearer {secret}"}
        elif self.auth_type == "api_key":
            return {"X-Api-Key": secret}
        elif self.auth_type == "basic":
            return {"Authorization": f"Basic {secret}"}
        return {"Authorization": f"Bearer {secret}"}

    def _filter_allowed_fields(self, payload: Any) -> Any:
        """Filters response dictionary or list of dicts to only allow approved fields."""
        if isinstance(payload, dict):
            filtered: dict[str, Any] = {}
            for k, v in payload.items():
                if k in self.allowed_fields or k.lower() in self.allowed_fields:
                    filtered[k] = self._filter_allowed_fields(v)
            return CredentialManager.sanitize_data(filtered)
        elif isinstance(payload, list):
            return [self._filter_allowed_fields(item) for item in payload]
        return payload

    async def health_check(self) -> HealthStatus:
        headers = self._get_auth_headers()
        url = f"{self.base_url}/health"

        try:
            client = await self._get_client(timeout_seconds=3.0)
            res = await client.get(url, headers=headers)
            latency = res.elapsed.total_seconds() * 1000 if hasattr(res, "elapsed") else 5.0
            is_ok = 200 <= res.status_code < 400
            return HealthStatus(
                is_healthy=is_ok,
                latency_ms=latency,
                details={"status_code": res.status_code, "url": url},
            )
        except Exception as e:
            return HealthStatus(
                is_healthy=False,
                latency_ms=0.0,
                details={"error": str(e), "url": url},
            )

    async def _execute_internal(
        self,
        operation: str,
        verified_customer_id: str,
        parameters: dict[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any] | list[dict[str, Any]] | None:
        # 1. Resilience gates
        await self.rate_limiter.acquire(1)
        await self.circuit_breaker.check_permitted()

        # 2. Build target URL with mandatory verified_customer_id scoping
        if operation == "get_orders":
            path = f"/customers/{verified_customer_id}/orders"
        elif operation == "get_order_details":
            order_id = parameters.get("order_id")
            if not order_id or not isinstance(order_id, str) or not order_id.strip():
                raise ConnectorValidationError("Parameter 'order_id' is required for get_order_details")
            clean_order_id = order_id.strip()
            path = f"/customers/{verified_customer_id}/orders/{clean_order_id}"
        else:
            raise ConnectorValidationError(f"Unsupported operation '{operation}'")

        full_url = f"{self.base_url}{path}"
        headers = self._get_auth_headers()
        headers["Accept"] = "application/json"

        client = await self._get_client(timeout_seconds=timeout_seconds)
        try:
            res = await client.get(full_url, headers=headers)
        except httpx.TimeoutException as e:
            await self.circuit_breaker.record_failure()
            raise ConnectorTimeoutError(f"HTTP request timed out after {timeout_seconds}s") from e
        except httpx.RequestError as e:
            await self.circuit_breaker.record_failure()
            raise EnterpriseConnectorError(f"HTTP connection failed: {e}") from e

        # 3. Handle response status codes
        if res.status_code == 404:
            # 404 is valid business missing entity, not upstream breakdown
            await self.circuit_breaker.record_success()
            raise ConnectorNotFoundError(
                f"Entity not found for customer '{verified_customer_id}' at '{path}'"
            )
        elif res.status_code == 429:
            await self.circuit_breaker.record_failure()
            raise ConnectorRateLimitError("Upstream REST API rate limit exceeded (HTTP 429)")
        elif res.status_code >= 500:
            await self.circuit_breaker.record_failure()
            raise EnterpriseConnectorError(f"Upstream server error (HTTP {res.status_code})")
        elif res.status_code >= 400:
            await self.circuit_breaker.record_failure()
            raise EnterpriseConnectorError(f"Upstream client error (HTTP {res.status_code})")

        # 4. Successful parsing & sanitization
        await self.circuit_breaker.record_success()
        try:
            raw_json = res.json()
        except Exception as e:
            raise EnterpriseConnectorError(f"Invalid JSON received from upstream: {e}") from e

        filtered = self._filter_allowed_fields(raw_json)
        return filtered

