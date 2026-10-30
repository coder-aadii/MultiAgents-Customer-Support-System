from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.errors import ForbiddenError
from app.config import settings
from app.models.audit_event import ActorType, AuditEvent
from app.models.conversation import Conversation, ConversationStatus
from app.models.visitor import Visitor
from app.models.visitor_session import VisitorSession
from app.models.widget_settings import WidgetSettings
from app.schemas.visitor import WidgetSessionResponse
from app.security.tokens import generate_opaque_token, hash_token


def is_allowed_origin(request_origin: str | None, configured_origin: str) -> bool:
    """Validate request Origin exactly against configured allowed origin."""
    if not request_origin:
        return False

    normalized_req = request_origin.strip().rstrip("/")
    normalized_cfg = configured_origin.strip().rstrip("/")

    # Exact comparison
    if normalized_req == normalized_cfg:
        return True

    # In development mode, allow localhost origins if configured
    if settings.MCSS_ENV == "development":
        if normalized_req in settings.cors_origins:
            return True

    return False


async def create_or_resume_session(
    db: AsyncSession,
    origin: str | None,
    raw_token: str | None = None,
    name: str | None = None,
    email: str | None = None,
    request_id: str | None = None,
) -> WidgetSessionResponse:
    """Create a new 24h visitor session or resume an existing one if token is valid."""
    # 1. Query widget settings for allowed origin
    settings_stmt = select(WidgetSettings).limit(1)
    result = await db.execute(settings_stmt)
    widget_setting = result.scalar_one_or_none()
    configured_origin = (
        widget_setting.allowed_origin if widget_setting else settings.WIDGET_ALLOWED_ORIGIN
    )

    # 2. Strict origin validation
    if not is_allowed_origin(origin, configured_origin):
        raise ForbiddenError("Origin not allowed for widget embedding")

    now = datetime.now(UTC)

    # 3. Check for presented bearer token to resume conversation
    if raw_token:
        token_hash = hash_token(raw_token)
        session_stmt = select(VisitorSession).where(
            VisitorSession.token_hash == token_hash,
            VisitorSession.revoked_at.is_(None),
            VisitorSession.expires_at > now,
        )
        session_res = await db.execute(session_stmt)
        session = session_res.scalar_one_or_none()
        if session:
            # Resume existing conversation
            return WidgetSessionResponse(
                session_token=raw_token,
                visitor_id=session.visitor_id,
                conversation_id=session.conversation_id,
                expires_at=session.expires_at,
            )

    # 4. Create new visitor, conversation, and session
    visitor = Visitor(
        name=name,
        email=email,
        created_at=now,
        last_seen_at=now,
    )
    db.add(visitor)
    await db.flush()

    conversation = Conversation(
        visitor_id=visitor.id,
        status=ConversationStatus.open,
        last_message_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(conversation)
    await db.flush()

    raw_session_token = generate_opaque_token()
    session_token_hash = hash_token(raw_session_token)
    expires_at = now + timedelta(hours=24)

    session = VisitorSession(
        visitor_id=visitor.id,
        conversation_id=conversation.id,
        token_hash=session_token_hash,
        origin=origin or configured_origin,
        expires_at=expires_at,
        created_at=now,
    )
    db.add(session)

    # Record audit event
    audit = AuditEvent(
        actor_type=ActorType.visitor,
        actor_id=visitor.id,
        event_type="visitor.session.created",
        resource_type="visitor_session",
        resource_id=session.id,
        metadata_json={"conversation_id": str(conversation.id), "request_id": request_id},
    )
    db.add(audit)
    await db.commit()

    return WidgetSessionResponse(
        session_token=raw_session_token,
        visitor_id=visitor.id,
        conversation_id=conversation.id,
        expires_at=expires_at,
    )
