from __future__ import annotations

import abc
import time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EnterpriseConnectorError(Exception):
    """Base exception for all enterprise connector operations."""


class ConnectorAuthError(EnterpriseConnectorError):
    """Raised when customer assurance level is insufficient (< 2) or token is invalid."""


class ConnectorNotFoundError(EnterpriseConnectorError):
    """Raised when the requested entity does not exist or does not belong to verified customer."""


class ConnectorTimeoutError(EnterpriseConnectorError):
    """Raised when connector call exceeds bounded execution timeout."""


class ConnectorRateLimitError(EnterpriseConnectorError):
    """Raised when connector invocation exceeds rate limit quota."""


class ConnectorCircuitOpenError(EnterpriseConnectorError):
    """Raised when circuit breaker trips due to upstream failure thresholds."""


class ConnectorValidationError(EnterpriseConnectorError):
    """Raised when query parameters fail schema validation or contain unsafe payloads."""


class HealthStatus(BaseModel):
    """Health check diagnostic result for a connector."""

    is_healthy: bool = Field(description="True if connector can communicate with upstream")
    latency_ms: float = Field(description="Round-trip ping latency in milliseconds")
    details: dict[str, Any] = Field(default_factory=dict, description="Diagnostic metadata")

    model_config = ConfigDict(frozen=True)


class ConnectorResult(BaseModel):
    """Standardized result envelope returned by all enterprise connectors."""

    success: bool = Field(description="Whether the query succeeded")
    connector_name: str = Field(description="Name of the executing connector")
    operation: str = Field(description="Invoked operation (e.g. 'get_order_details')")
    data: dict[str, Any] | list[dict[str, Any]] | None = Field(
        default=None, description="Sanitized business data entity or entity list"
    )
    error_code: str | None = Field(
        default=None, description="Normalized error code (e.g. 'NOT_FOUND', 'TIMEOUT')"
    )
    error_message: str | None = Field(
        default=None, description="Human-readable error description without sensitive credentials"
    )
    execution_time_ms: int = Field(default=0, description="Execution time in milliseconds")
    cache_hit: bool = Field(default=False, description="Whether result was served from cache")

    model_config = ConfigDict(frozen=True)


class BaseEnterpriseConnector(abc.ABC):
    """Abstract base contract for all production-grade enterprise data connectors.

    Guarantees:
    1. Mandatory server-side customer ID scoping (`verified_customer_id`).
    2. Zero direct client access to external APIs.
    3. Strict credential isolation and log scrubbing.
    4. Resilient execution with bounded timeouts and standardized error envelope.
    """

    @property
    @abc.abstractmethod
    def connector_name(self) -> str:
        """Unique identifier for this connector."""
        raise NotImplementedError

    @property
    @abc.abstractmethod
    def supported_operations(self) -> list[str]:
        """List of operations exposed by this connector."""
        raise NotImplementedError

    @abc.abstractmethod
    async def health_check(self) -> HealthStatus:
        """Executes a diagnostic health check against upstream system."""
        raise NotImplementedError

    @abc.abstractmethod
    async def _execute_internal(
        self,
        operation: str,
        verified_customer_id: str,
        parameters: dict[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any] | list[dict[str, Any]] | None:
        """Concrete connector execution logic implemented by subclasses."""
        raise NotImplementedError

    async def execute_query(
        self,
        operation: str,
        verified_customer_id: str,
        parameters: dict[str, Any] | None = None,
        timeout_seconds: float = 5.0,
    ) -> ConnectorResult:
        """Executes a strictly authorized query.

        Enforces:
        - Non-empty verified_customer_id.
        - Operation validation against supported_operations.
        - Strict timeout bounds (maximum 10.0s).
        - Exception normalization into ConnectorResult with execution time.
        """
        start_time = time.perf_counter()
        params = parameters or {}

        # 1. Validate customer ID
        if not verified_customer_id or not isinstance(verified_customer_id, str) or not verified_customer_id.strip():
            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            return ConnectorResult(
                success=False,
                connector_name=self.connector_name,
                operation=operation,
                error_code="AUTHORIZATION_REQUIRED",
                error_message="Missing or invalid verified customer identifier (IAL >= 2 required)",
                execution_time_ms=elapsed_ms,
            )

        # 2. Validate operation
        if operation not in self.supported_operations:
            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            return ConnectorResult(
                success=False,
                connector_name=self.connector_name,
                operation=operation,
                error_code="UNSUPPORTED_OPERATION",
                error_message=f"Operation '{operation}' is not supported by connector '{self.connector_name}'",
                execution_time_ms=elapsed_ms,
            )

        # 3. Bound timeout (0.5s - 10.0s)
        bounded_timeout = max(0.5, min(float(timeout_seconds), 10.0))

        try:
            data = await self._execute_internal(
                operation=operation,
                verified_customer_id=verified_customer_id.strip(),
                parameters=params,
                timeout_seconds=bounded_timeout,
            )
            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            return ConnectorResult(
                success=True,
                connector_name=self.connector_name,
                operation=operation,
                data=data,
                execution_time_ms=elapsed_ms,
            )

        except ConnectorNotFoundError as e:
            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            return ConnectorResult(
                success=False,
                connector_name=self.connector_name,
                operation=operation,
                error_code="NOT_FOUND",
                error_message=str(e),
                execution_time_ms=elapsed_ms,
            )

        except ConnectorTimeoutError as e:
            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            return ConnectorResult(
                success=False,
                connector_name=self.connector_name,
                operation=operation,
                error_code="TIMEOUT",
                error_message=str(e),
                execution_time_ms=elapsed_ms,
            )

        except ConnectorRateLimitError as e:
            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            return ConnectorResult(
                success=False,
                connector_name=self.connector_name,
                operation=operation,
                error_code="RATE_LIMITED",
                error_message=str(e),
                execution_time_ms=elapsed_ms,
            )

        except ConnectorCircuitOpenError as e:
            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            return ConnectorResult(
                success=False,
                connector_name=self.connector_name,
                operation=operation,
                error_code="CIRCUIT_OPEN",
                error_message=str(e),
                execution_time_ms=elapsed_ms,
            )

        except ConnectorValidationError as e:
            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            return ConnectorResult(
                success=False,
                connector_name=self.connector_name,
                operation=operation,
                error_code="VALIDATION_ERROR",
                error_message=str(e),
                execution_time_ms=elapsed_ms,
            )

        except Exception as e:
            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            return ConnectorResult(
                success=False,
                connector_name=self.connector_name,
                operation=operation,
                error_code="UPSTREAM_ERROR",
                error_message="External connector invocation failed",
                execution_time_ms=elapsed_ms,
            )

