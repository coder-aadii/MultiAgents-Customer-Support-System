from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.main import app
from app.models.conversation import Conversation, ConversationStatus
from app.models.organization import Organization
from app.models.user import User, UserRole
from app.models.visitor import Visitor
from app.operations.notes import InternalNotesService
from app.security.password import hash_password
from app.security.tokens import create_access_jwt


@pytest_asyncio.fixture
async def async_db():
    engine = create_async_engine(settings.DATABASE_URL)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def notes_setup(async_db: AsyncSession):
    org = Organization(slug=f"notes-org-{uuid.uuid4().hex[:6]}", name="Notes Org", is_active=True)
    async_db.add(org)
    await async_db.flush()

    admin = User(
        organization_id=org.id,
        email=f"admin-{uuid.uuid4().hex[:6]}@example.com",
        password_hash=hash_password("Pass123!"),
        display_name="Supervisor Admin",
        role=UserRole.admin,
        is_active=True,
    )
    assigned_agent = User(
        organization_id=org.id,
        email=f"assigned-{uuid.uuid4().hex[:6]}@example.com",
        password_hash=hash_password("Pass123!"),
        display_name="Assigned Agent",
        role=UserRole.staff,
        is_active=True,
    )
    other_agent = User(
        organization_id=org.id,
        email=f"other-{uuid.uuid4().hex[:6]}@example.com",
        password_hash=hash_password("Pass123!"),
        display_name="Other Agent",
        role=UserRole.staff,
        is_active=True,
    )
    async_db.add_all([admin, assigned_agent, other_agent])
    await async_db.flush()

    visitor = Visitor(organization_id=org.id, name="Customer Note Vis")
    async_db.add(visitor)
    await async_db.flush()

    conv = Conversation(
        organization_id=org.id,
        visitor_id=visitor.id,
        assigned_user_id=assigned_agent.id,
        assigned_agent_id=assigned_agent.id,
        status=ConversationStatus.staff_active,
    )
    async_db.add(conv)
    await async_db.commit()

    return org, admin, assigned_agent, other_agent, conv


@pytest.mark.asyncio
async def test_internal_note_saved_and_retrieved_by_staff(
    async_db: AsyncSession, notes_setup
):
    """Internal notes can be saved by staff and retrieved via API."""
    org, admin, assigned_agent, other_agent, conv = notes_setup

    note = await InternalNotesService.add_note(
        db=async_db,
        conversation_id=conv.id,
        author_user=assigned_agent,
        note_text="Customer requested expedited replacement unit.",
        is_whisper=False,
    )
    assert note.id is not None
    assert note.note_text == "Customer requested expedited replacement unit."
    assert note.is_whisper is False

    notes = await InternalNotesService.list_notes(async_db, conv.id, assigned_agent)
    assert len(notes) == 1
    assert notes[0].id == note.id


@pytest.mark.asyncio
async def test_supervisor_whisper_visible_only_to_assigned_agent(
    async_db: AsyncSession, notes_setup
):
    """Supervisor whisper is visible only to assigned agent, the author, and admins."""
    org, admin, assigned_agent, other_agent, conv = notes_setup

    # Admin whispers coaching note
    whisper = await InternalNotesService.add_note(
        db=async_db,
        conversation_id=conv.id,
        author_user=admin,
        note_text="Offer 10% coupon code if they hesitate.",
        is_whisper=True,
    )

    # 1. Admin (author) sees it
    admin_notes = await InternalNotesService.list_notes(async_db, conv.id, admin)
    assert any(n.id == whisper.id for n in admin_notes)

    # 2. Assigned Agent sees it
    assigned_notes = await InternalNotesService.list_notes(async_db, conv.id, assigned_agent)
    assert any(n.id == whisper.id for n in assigned_notes)

    # 3. Other Agent does NOT see the whisper
    other_notes = await InternalNotesService.list_notes(async_db, conv.id, other_agent)
    assert not any(n.id == whisper.id for n in other_notes)


@pytest.mark.asyncio
async def test_internal_note_strictly_excluded_from_widget_api(
    async_db: AsyncSession, notes_setup
):
    """Zero-leakage guarantee: Internal notes and whispers never appear on public widget message endpoints."""
    org, admin, assigned_agent, other_agent, conv = notes_setup

    # Add regular note and whisper
    await InternalNotesService.add_note(
        async_db, conv.id, admin, "INTERNAL STAFF NOTE ONLY", is_whisper=False
    )
    await InternalNotesService.add_note(
        async_db, conv.id, admin, "SUPERVISOR WHISPER SECRET", is_whisper=True
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Public widget listing messages for conversation
        resp = await client.get(f"/api/v1/widget/conversations/{conv.id}/messages")
        # Widget requires visitor session or returns 401/404, never leaking internal notes
        assert resp.status_code in (401, 404)
        assert "INTERNAL STAFF NOTE ONLY" not in resp.text
        assert "SUPERVISOR WHISPER SECRET" not in resp.text


@pytest.mark.asyncio
async def test_internal_note_cross_tenant_isolation_blocked(
    async_db: AsyncSession, notes_setup
):
    """Staff user of Org B cannot access or create internal notes for Org A conversations."""
    org, admin, assigned_agent, other_agent, conv = notes_setup

    org_b = Organization(slug=f"org-b-{uuid.uuid4().hex[:6]}", name="Attacker Org", is_active=True)
    async_db.add(org_b)
    await async_db.flush()

    user_b = User(
        organization_id=org_b.id,
        email=f"spy-{uuid.uuid4().hex[:6]}@example.com",
        password_hash=hash_password("Pass123!"),
        display_name="Spy Agent",
        role=UserRole.admin,
        is_active=True,
    )
    async_db.add(user_b)
    await async_db.commit()

    # 1. Direct Service check: user_b cannot list notes of conv (raises NotFoundError)
    from app.common.errors import NotFoundError
    with pytest.raises(NotFoundError):
        await InternalNotesService.list_notes(async_db, conv.id, user_b)

    # user_b cannot add note to conv
    with pytest.raises(NotFoundError):
        await InternalNotesService.add_note(async_db, conv.id, user_b, "Unauthorized injection note")

    # 2. HTTP Endpoint check
    token_b = create_access_jwt(
        subject=str(user_b.id),
        role=user_b.role.value,
        additional_claims={"org_id": str(org_b.id), "org_slug": org_b.slug},
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # GET /conversations/{id}/notes
        resp = await client.get(
            f"/api/v1/conversations/{conv.id}/notes",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert resp.status_code == 404

        # POST /conversations/{id}/notes
        resp_post = await client.post(
            f"/api/v1/conversations/{conv.id}/notes",
            headers={"Authorization": f"Bearer {token_b}"},
            json={"note_text": "Cross tenant injected note"},
        )
        assert resp_post.status_code == 404


