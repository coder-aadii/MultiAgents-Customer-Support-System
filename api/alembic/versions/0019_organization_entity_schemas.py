"""Add entity_schemas_json column to organizations table.

Revision ID: 0019_organization_entity_schemas
Revises: 0018_visitor_search_indices
Create Date: 2026-11-11
"""

from collections.abc import Sequence
import json

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0019_organization_entity_schemas"
down_revision: str | None = "0018_visitor_search_indices"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_OLIST_SCHEMAS = {
    "entities": [
        {
            "name": "order",
            "display_name": "Customer Order",
            "description": "E-commerce purchase order with payment, status, and item details.",
            "identifier_pattern": r"\b(?:ORD-[0-9A-Za-z]{3,32}|[0-9a-fA-F]{32}|#[0-9A-Za-z]{2,32}|\d{3,16})\b",
            "primary_key": "order_id",
            "capabilities": ["read", "cancel"],
            "fields": [
                {"name": "order_id", "field_type": "string", "required": True, "is_identifier": True},
                {"name": "status", "field_type": "string", "required": True},
                {"name": "total_amount", "field_type": "float"},
                {"name": "customer_id", "field_type": "string"}
            ]
        },
        {
            "name": "shipment",
            "display_name": "Fulfillment Shipment",
            "description": "Logistics carrier shipment with tracking code and delivery milestones.",
            "identifier_pattern": r"\b(?:TRK|SHP|BR)-[0-9A-Z]{3,32}\b",
            "primary_key": "tracking_number",
            "capabilities": ["read"],
            "fields": [
                {"name": "tracking_number", "field_type": "string", "required": True, "is_identifier": True},
                {"name": "carrier", "field_type": "string"},
                {"name": "status", "field_type": "string"},
                {"name": "estimated_delivery", "field_type": "datetime"}
            ]
        },
        {
            "name": "refund",
            "display_name": "Payment Refund",
            "description": "Payment reversal or charge credit transaction record.",
            "identifier_pattern": r"\bREF-[0-9A-Z]{3,32}\b",
            "primary_key": "refund_id",
            "capabilities": ["read", "approve"],
            "fields": [
                {"name": "refund_id", "field_type": "string", "required": True, "is_identifier": True},
                {"name": "order_id", "field_type": "string", "required": True},
                {"name": "amount", "field_type": "float", "required": True},
                {"name": "status", "field_type": "string"}
            ]
        },
        {
            "name": "customer",
            "display_name": "Customer Profile",
            "description": "Verified customer identity, email, and contact record.",
            "identifier_pattern": r"\b(?:CUST-[0-9A-Za-z]{3,32}|cust_[0-9a-zA-Z_]+)\b",
            "primary_key": "customer_id",
            "capabilities": ["read"],
            "fields": [
                {"name": "customer_id", "field_type": "string", "required": True, "is_identifier": True},
                {"name": "email", "field_type": "string", "required": True},
                {"name": "name", "field_type": "string"}
            ]
        }
    ],
    "actions": [
        {
            "action_type": "cancel_order",
            "description": "Cancel an active processing order prior to warehouse dispatch.",
            "entity_type": "order",
            "requires_approval": True,
            "allowed_roles": ["admin", "owner", "supervisor"]
        },
        {
            "action_type": "process_refund",
            "description": "Issue payment reversal for returned or damaged goods.",
            "entity_type": "refund",
            "requires_approval": True,
            "allowed_roles": ["admin", "owner"]
        }
    ]
}


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column(
            "entity_schemas_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )

    # Seed default Olist entity schema for the default organization
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE organizations SET entity_schemas_json = CAST(:schemas AS jsonb) "
            "WHERE slug = 'default' OR entity_schemas_json = '{}'::jsonb"
        ),
        {"schemas": json.dumps(DEFAULT_OLIST_SCHEMAS)},
    )


def downgrade() -> None:
    op.drop_column("organizations", "entity_schemas_json")
