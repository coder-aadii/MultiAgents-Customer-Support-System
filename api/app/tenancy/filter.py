from __future__ import annotations

import uuid
from typing import Any, TypeVar

from sqlalchemy import Select, and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.tenancy.context import TenantContext

T = TypeVar("T")


class UnscopedQuerySecurityError(Exception):
    """Raised when an attempt is made to query a tenant-aware table without organization scoping."""


class TenantQueryScoper:
    """Enforces strict tenant isolation across SQLAlchemy query construction."""

    @classmethod
    def apply_scope(
        cls,
        stmt: Select,
        model: type[Any],
        org_id: uuid.UUID | None = None,
        allow_fallback: bool = True,
    ) -> Select:
        """Appends mandatory WHERE organization_id = :org_id filter to the query."""
        effective_org_id = org_id or (TenantContext.get_tenant_id() if allow_fallback else None)
        if not effective_org_id:
            raise UnscopedQuerySecurityError(
                f"Query on tenant model {model.__name__} has no active organization context."
            )

        if not hasattr(model, "organization_id"):
            return stmt

        return stmt.where(model.organization_id == effective_org_id)

    @classmethod
    async def get_scoped(
        cls,
        db: AsyncSession,
        model: type[T],
        entity_id: uuid.UUID,
        org_id: uuid.UUID | None = None,
    ) -> T | None:
        """Retrieves a single entity strictly within the specified or active tenant context.

        Returns None if entity does not exist OR belongs to another tenant.
        """
        stmt = select(model).where(model.id == entity_id)  # type: ignore
        scoped_stmt = cls.apply_scope(stmt, model, org_id=org_id)
        res = await db.execute(scoped_stmt)
        return res.scalar_one_or_none()
