from __future__ import annotations

import contextvars
import uuid

DEFAULT_ORGANIZATION_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
DEFAULT_ORGANIZATION_SLUG = "default"

_current_tenant_id: contextvars.ContextVar[uuid.UUID] = contextvars.ContextVar(
    "current_tenant_id", default=DEFAULT_ORGANIZATION_ID
)
_current_tenant_slug: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_tenant_slug", default=DEFAULT_ORGANIZATION_SLUG
)


class TenantContext:
    """Manages thread-local / async-task scoped tenant organization context."""

    @classmethod
    def get_tenant_id(cls) -> uuid.UUID:
        return _current_tenant_id.get()

    @classmethod
    def get_tenant_slug(cls) -> str:
        return _current_tenant_slug.get()

    @classmethod
    def set_tenant(cls, org_id: uuid.UUID, slug: str = "custom") -> tuple[contextvars.Token, contextvars.Token]:
        t1 = _current_tenant_id.set(org_id)
        t2 = _current_tenant_slug.set(slug)
        return t1, t2

    @classmethod
    def reset(cls, tokens: tuple[contextvars.Token, contextvars.Token]) -> None:
        t1, t2 = tokens
        _current_tenant_id.reset(t1)
        _current_tenant_slug.reset(t2)

