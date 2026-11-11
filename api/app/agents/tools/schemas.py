"""Strict schemas and OpenAI function specifications for dynamic read-only tools."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DynamicToolCall(BaseModel):
    """Model-proposed call. Identity fields are never trusted from this schema."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    arguments: dict[str, Any] = Field(default_factory=dict)


class DynamicToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    data: dict[str, Any] | list[dict[str, Any]] | None = None
    execution_time_ms: int = Field(ge=0)


READ_ONLY_TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "get_order_summary": {
        "description": "Get one verified customer's order by its order ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string", "pattern": "^ORD-[0-9A-Z]{3,32}$"}
            },
            "required": ["entity_id"],
            "additionalProperties": False,
        },
    },
    "list_customer_orders": {
        "description": "List orders belonging to the verified customer.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "get_shipment_status": {
        "description": "Get one verified customer's shipment or carrier status.",
        "parameters": {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string", "pattern": "^(?:TRK|SHP)-[0-9A-Z]{3,32}$"}
            },
            "required": ["entity_id"],
            "additionalProperties": False,
        },
    },
    "get_refund_status": {
        "description": "Get one verified customer's refund status.",
        "parameters": {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string", "pattern": "^REF-[0-9A-Z]{3,32}$"}
            },
            "required": ["entity_id"],
            "additionalProperties": False,
        },
    },
    "connector_query": {
        "description": "Execute an allowlisted, read-only enterprise connector query.",
        "parameters": {
            "type": "object",
            "properties": {
                "connector_name": {"type": "string", "pattern": "^[a-z][a-z0-9_]{0,63}$"},
                "operation": {"type": "string", "pattern": "^[a-z][a-z0-9_]{0,63}$"},
                "parameters": {"type": "object"},
            },
            "required": ["connector_name", "operation"],
            "additionalProperties": False,
        },
    },
}


class DynamicToolSchemaCompiler:
    """Generates read-only tool schemas dynamically from connector capability definitions or tenant entity schemas."""

    @classmethod
    def compile_for_entity(cls, entity_name: str, identifier_pattern: str | None = None, description: str = "") -> dict[str, dict[str, Any]]:
        clean_name = entity_name.strip().lower()
        pattern = identifier_pattern or r"^[0-9A-Za-z_-]{2,64}$"
        return {
            f"get_{clean_name}_summary": {
                "description": description or f"Get one verified customer's {clean_name} by ID.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "entity_id": {"type": "string", "pattern": pattern}
                    },
                    "required": ["entity_id"],
                    "additionalProperties": False,
                },
            },
            f"list_customer_{clean_name}s": {
                "description": f"List {clean_name}s belonging to the verified customer.",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        }

    @classmethod
    def compile_all(cls, entities: list[dict[str, Any]] | None = None) -> dict[str, dict[str, Any]]:
        schemas = dict(READ_ONLY_TOOL_SCHEMAS)
        if entities:
            for ent in entities:
                name = ent.get("name")
                if name:
                    schemas.update(cls.compile_for_entity(
                        name,
                        identifier_pattern=ent.get("identifier_pattern"),
                        description=ent.get("description", ""),
                    ))
        return schemas


def as_openai_tools(tool_names: set[str], extra_schemas: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Render server-registered capabilities into OpenAI function-tool format."""
    available = dict(READ_ONLY_TOOL_SCHEMAS)
    if extra_schemas:
        available.update(extra_schemas)
    return [
        {
            "type": "function",
            "function": {"name": name, **available[name]},
        }
        for name in sorted(tool_names)
        if name in available
    ]
