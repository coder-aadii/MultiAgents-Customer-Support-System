"""Zero-trust validation of model-proposed tool arguments."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any

from app.agents.tools.schemas import READ_ONLY_TOOL_SCHEMAS


class SecurityViolationError(ValueError):
    """Raised for any dynamic call that could escape its read-only sandbox."""


class DynamicToolValidationError(ValueError):
    """Raised before a malformed dynamic request reaches a connector."""


_IDENTITY_FIELDS = frozenset({"customer_id", "verified_customer_id", "tenant_id", "organization_id"})
_FORBIDDEN_QUERY_FIELDS = frozenset({"query", "sql", "sql_query", "statement", "command"})
_IDENTIFIER_PATTERNS = {
    "get_order_summary": {"entity_id": re.compile(r"^ORD-[0-9A-Z]{3,32}$")},
    "get_shipment_status": {"entity_id": re.compile(r"^(?:TRK|SHP)-[0-9A-Z]{3,32}$")},
    "get_refund_status": {"entity_id": re.compile(r"^REF-[0-9A-Z]{3,32}$")},
    "connector_query": {
        "connector_name": re.compile(r"^[a-z][a-z0-9_]{0,63}$"),
        "operation": re.compile(r"^[a-z][a-z0-9_]{0,63}$"),
    },
}


@dataclass(frozen=True, slots=True)
class SanitizedToolCall:
    name: str
    parameters: dict[str, Any]
    tenant_id: uuid.UUID
    verified_customer_id: str


def sanitize_tool_call(
    name: str,
    arguments: dict[str, Any],
    tenant_id: uuid.UUID,
    verified_customer_id: str,
) -> SanitizedToolCall:
    """Drop spoofed identity claims and validate a fixed parameter whitelist."""
    specification = READ_ONLY_TOOL_SCHEMAS.get(name)
    if specification is None:
        raise DynamicToolValidationError(f"unknown dynamic tool '{name}'")
    if not isinstance(arguments, dict):
        raise DynamicToolValidationError("tool arguments must be an object")
    if not verified_customer_id.strip():
        raise SecurityViolationError("verified customer identity is required")

    allowed_fields = set(specification["parameters"]["properties"])
    supplied_fields = set(arguments) - _IDENTITY_FIELDS
    unexpected_fields = supplied_fields - allowed_fields
    if unexpected_fields:
        raise DynamicToolValidationError(
            f"unexpected tool parameters: {sorted(unexpected_fields)}"
        )
    sanitized = {key: value for key, value in arguments.items() if key in allowed_fields}
    for field in specification["parameters"].get("required", []):
        if field not in sanitized:
            raise DynamicToolValidationError(f"missing required tool parameter '{field}'")

    for field, pattern in _IDENTIFIER_PATTERNS.get(name, {}).items():
        value = sanitized.get(field)
        if not isinstance(value, str) or not pattern.fullmatch(value):
            raise DynamicToolValidationError(f"invalid '{field}' format")
        sanitized[field] = value.strip()
    if "parameters" in sanitized:
        if not isinstance(sanitized["parameters"], dict):
            raise DynamicToolValidationError("connector query parameters must be an object")
        sanitized["parameters"] = _sanitize_nested_parameters(sanitized["parameters"])

    return SanitizedToolCall(
        name=name,
        parameters=sanitized,
        tenant_id=tenant_id,
        verified_customer_id=verified_customer_id.strip(),
    )


def _sanitize_nested_parameters(parameters: dict[str, Any]) -> dict[str, Any]:
    """Permit bounded JSON data, never free-form query text or identity overrides."""
    clean: dict[str, Any] = {}
    for key, value in parameters.items():
        normalized_key = str(key).lower()
        if normalized_key in _IDENTITY_FIELDS:
            continue
        if normalized_key in _FORBIDDEN_QUERY_FIELDS:
            raise SecurityViolationError("free-form query parameters are forbidden")
        if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{0,63}", str(key)):
            raise DynamicToolValidationError("connector parameter name is invalid")
        if isinstance(value, dict):
            clean[str(key)] = _sanitize_nested_parameters(value)
        elif isinstance(value, list):
            if len(value) > 20:
                raise DynamicToolValidationError("connector parameter list is too large")
            clean[str(key)] = [
                _sanitize_nested_parameters(item) if isinstance(item, dict) else item for item in value
            ]
        elif isinstance(value, str):
            if len(value) > 256:
                raise DynamicToolValidationError("connector parameter value is too long")
            clean[str(key)] = value
        elif isinstance(value, int | float | bool) or value is None:
            clean[str(key)] = value
        else:
            raise DynamicToolValidationError("connector parameter value type is not allowed")
    return clean
