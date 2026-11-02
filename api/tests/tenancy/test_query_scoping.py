from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models.conversation import Conversation, ConversationStatus
from app.models.organization import Organization
from app.models.visitor import Visitor
from app.tenancy.context import DEFAULT_ORGANIZATION_ID, TenantContext
from app.tenancy.filter import TenantQueryScoper, UnscopedQuerySecurityError


@pytest_asyncio.fixture
async def async_db():
    engine = create_async_engine(settings.DATABASE_URL)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def two_orgs(async_db: AsyncSession) -> tuple[Organization, Organization]:
    org_a = Organization(
        slug=f"org-a-{uuid.uuid4().hex[:6]}",
        name="Organization Alpha",
        is_active=True,
    )
    org_b = Organization(
        slug=f"org-b-{uuid.uuid4().hex[:6]}",
        name="Organization Beta",
        is_active=True,
    )
    async_db.add_all([org_a, org_b])
    await async_db.commit()
    return org_a, org_b


@pytest.mark.asyncio
async def test_query_scoping_automatically_applies_org_filter(
    async_db: AsyncSession, two_orgs: tuple[Organization, Organization]
):
    """Test that TenantQueryScoper strictly segregates conversations between orgs."""
    org_a, org_b = two_orgs

    # Create visitor & conversation in Org A
    visitor_a = Visitor(organization_id=org_a.id, name="User A")
    async_db.add(visitor_a)
    await async_db.flush()

    conv_a = Conversation(
        organization_id=org_a.id,
        visitor_id=visitor_a.id,
        status=ConversationStatus.open,
    )
    async_db.add(conv_a)

    # Create visitor & conversation in Org B
    visitor_b = Visitor(organization_id=org_b.id, name="User B")
    async_db.add(visitor_b)
    await async_db.flush()

    conv_b = Conversation(
        organization_id=org_b.id,
        visitor_id=visitor_b.id,
        status=ConversationStatus.open,
    )
    async_db.add(conv_b)
    await async_db.commit()

    # Query within Org A context
    tokens = TenantContext.set_tenant(org_a.id, org_a.slug)
    try:
        stmt = TenantQueryScoper.apply_scope(select(Conversation), Conversation)
        res = await async_db.execute(stmt)
        convs_in_a = list(res.scalars().all())

        # Org A must see its own conversation and NEVER Org B's
        assert any(c.id == conv_a.id for c in convs_in_a)
        assert all(c.id != conv_b.id for c in convs_in_a)
    finally:
        TenantContext.reset(tokens)


@pytest.mark.asyncio
async def test_cross_org_conversation_access_returns_404(
    async_db: AsyncSession, two_orgs: tuple[Organization, Organization]
):
    """Test that attempting to fetch Org B's entity while scoped to Org A returns None."""
    org_a, org_b = two_orgs

    visitor_b = Visitor(organization_id=org_b.id, name="Visitor B")
    async_db.add(visitor_b)
    await async_db.flush()

    conv_b = Conversation(
        organization_id=org_b.id,
        visitor_id=visitor_b.id,
        status=ConversationStatus.open,
    )
    async_db.add(conv_b)
    await async_db.commit()

    # Attempt to retrieve conv_b while scoped to Org A
    scoped_result = await TenantQueryScoper.get_scoped(
        db=async_db,
        model=Conversation,
        entity_id=conv_b.id,
        org_id=org_a.id,
    )
    assert scoped_result is None

    # Retrieve conv_b with correct Org B scope
    valid_result = await TenantQueryScoper.get_scoped(
        db=async_db,
        model=Conversation,
        entity_id=conv_b.id,
        org_id=org_b.id,
    )
    assert valid_result is not None
    assert valid_result.id == conv_b.id


def test_unscoped_query_raises_security_exception():
    """Verify that attempting to scope a tenant-aware query with None raises UnscopedQuerySecurityError."""
    stmt = select(Conversation)
    with pytest.raises(UnscopedQuerySecurityError):
        TenantQueryScoper.apply_scope(stmt, Conversation, org_id=None, allow_fallback=False)
