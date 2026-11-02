from __future__ import annotations

from pydantic import BaseModel, Field

from app.config import settings


class OrganizationContext(BaseModel):
    """Explicit server-side context for single-tenant organization boundary."""

    organization_id: str = Field(
        default_factory=lambda: settings.ORGANIZATION_ID,
        description="Immutable organization identifier",
    )
    organization_name: str = Field(
        default_factory=lambda: settings.ORGANIZATION_NAME,
        description="Organization display name",
    )
    is_active: bool = Field(
        default=True,
        description="Whether organization is active and authorized",
    )


def get_organization_context() -> OrganizationContext:
    """FastAPI dependency providing explicit server-side organization context."""
    return OrganizationContext(
        organization_id=settings.ORGANIZATION_ID,
        organization_name=settings.ORGANIZATION_NAME,
        is_active=True,
    )

