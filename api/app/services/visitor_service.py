from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.errors import ForbiddenError, NotFoundError
from app.config import settings
from app.models.audit_event import ActorType, AuditEvent
from app.models.conversation import Conversation, ConversationStatus
from app.models.visitor import Visitor
from app.models.visitor_session import VisitorSession
from app.models.widget_settings import WidgetSettings
from app.schemas.visitor import WidgetSessionResponse
from app.security.customer_auth import (
    CustomerAuthError,
    CustomerTokenVerifier,
    VerifiedCustomerIdentity,
)
from app.security.tokens import generate_opaque_token, hash_token
from app.tenancy.context import TenantContext

logger = structlog.get_logger()


def is_allowed_origin(request_origin: str | None, configured_origin: str) -> bool:
    """Validate request Origin exactly against configured allowed origin."""
    if not request_origin:
        return False

    normalized_req = request_origin.strip().rstrip("/")
    normalized_cfg = configured_origin.strip().rstrip("/")

    # Exact comparison
    if normalized_req == normalized_cfg:
        return True

    # In development or test mode, allow configured cors origins and local widget testbed origins
    if settings.MCSS_ENV in ("development", "test"):
        if normalized_req in settings.cors_origins or normalized_req in (
            "http://localhost:5174",
            "http://127.0.0.1:5174",
            "http://widget:5174",
        ):
            return True

    return False


async def create_or_resume_session(
    db: AsyncSession,
    origin: str | None,
    raw_token: str | None = None,
    customer_token: str | None = None,
    name: str | None = None,
    email: str | None = None,
    request_id: str | None = None,
) -> WidgetSessionResponse:
    """Create a new 24h visitor session or resume an existing one.

    Enforces:
    - Origin verification.
    - Zero-trust customer token validation (IAL 0 to 2).
    - Revocation on account-switching or when customer credentials drop.
    - Zero leakage of raw customer JWTs.
    """
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

    # 3. Validate customer token if supplied
    verified_identity: VerifiedCustomerIdentity | None = None
    if customer_token:
        try:
            verifier = CustomerTokenVerifier()
            verified_identity = verifier.verify_token(customer_token)
        except CustomerAuthError as exc:
            # Safe logging: log reason/type, NEVER log raw token
            logger.warning(
                "customer_token_verification_failed",
                error_type=exc.__class__.__name__,
                request_id=request_id,
            )
            verified_identity = None

    # 4. Check for presented bearer session token
    if raw_token:
        token_hash = hash_token(raw_token)
        session_stmt = (
            select(VisitorSession)
            .where(
                VisitorSession.token_hash == token_hash,
                VisitorSession.revoked_at.is_(None),
                VisitorSession.expires_at > now,
            )
            .with_for_update()
        )
        session_res = await db.execute(session_stmt)
        existing_session = session_res.scalar_one_or_none()

        if existing_session is not None:
            # Case A: Previously authenticated customer session (IAL == 2)
            if existing_session.identity_assurance_level == 2:
                # If no valid customer token was presented, or token expired/invalid:
                # Invalidate existing session so anonymous visitor cannot inherit verified session
                if verified_identity is None:
                    existing_session.revoked_at = now
                    # Close previous verified conversation
                    conv_res = await db.execute(
                        select(Conversation)
                        .where(Conversation.id == existing_session.conversation_id)
                        .with_for_update()
                    )
                    prev_conv = conv_res.scalar_one_or_none()
                    if prev_conv and prev_conv.status == ConversationStatus.open:
                        prev_conv.status = ConversationStatus.closed
                        prev_conv.updated_at = now

                    db.add(
                        AuditEvent(
                            actor_type=ActorType.visitor,
                            actor_id=existing_session.visitor_id,
                            event_type="visitor.session.identity_dropped",
                            resource_type="visitor_session",
                            resource_id=existing_session.id,
                            metadata_json={
                                "conversation_id": str(existing_session.conversation_id),
                                "request_id": request_id,
                                "reason": "unauthenticated_customer_token",
                            },
                        )
                    )
                    # Proceed to create a fresh anonymous session below
                # If a different customer is presenting a token (Account switch)
                elif verified_identity.customer_id != existing_session.verified_customer_id:
                    existing_session.revoked_at = now
                    conv_res = await db.execute(
                        select(Conversation)
                        .where(Conversation.id == existing_session.conversation_id)
                        .with_for_update()
                    )
                    prev_conv = conv_res.scalar_one_or_none()
                    if prev_conv and prev_conv.status == ConversationStatus.open:
                        prev_conv.status = ConversationStatus.closed
                        prev_conv.updated_at = now

                    db.add(
                        AuditEvent(
                            actor_type=ActorType.visitor,
                            actor_id=existing_session.visitor_id,
                            event_type="visitor.session.account_switched",
                            resource_type="visitor_session",
                            resource_id=existing_session.id,
                            metadata_json={
                                "conversation_id": str(existing_session.conversation_id),
                                "request_id": request_id,
                                "old_customer_id": existing_session.verified_customer_id,
                                "new_customer_id": verified_identity.customer_id,
                            },
                        )
                    )
                    # Proceed to create a fresh session for the new customer below
                else:
                    # Same customer continuing: refresh token hash and expiration
                    existing_session.customer_token_hash = verified_identity.token_hash
                    existing_session.customer_token_expires_at = verified_identity.expires_at
                    existing_session.identity_assurance_level = 2
                    await db.commit()
                    return WidgetSessionResponse(
                        session_token=raw_token,
                        visitor_token=raw_token,
                        visitor_id=existing_session.visitor_id,
                        conversation_id=existing_session.conversation_id,
                        expires_at=existing_session.expires_at,
                        identity_assurance_level=2,
                        verified_customer_id=existing_session.verified_customer_id,
                    )

            # Case B: Previously anonymous session (IAL == 0)
            else:
                if verified_identity is None:
                    # Continue existing anonymous conversation
                    return WidgetSessionResponse(
                        session_token=raw_token,
                        visitor_token=raw_token,
                        visitor_id=existing_session.visitor_id,
                        conversation_id=existing_session.conversation_id,
                        expires_at=existing_session.expires_at,
                        identity_assurance_level=0,
                        verified_customer_id=None,
                    )
                else:
                    # Anonymous visitor logged into host portal: upgrade to IAL 2
                    existing_session.verified_customer_id = verified_identity.customer_id
                    existing_session.customer_token_hash = verified_identity.token_hash
                    existing_session.customer_token_expires_at = verified_identity.expires_at
                    existing_session.identity_assurance_level = 2

                    if verified_identity.email:
                        visitor_to_update = await db.scalar(
                            select(Visitor).where(Visitor.id == existing_session.visitor_id)
                        )
                        if visitor_to_update and not visitor_to_update.email:
                            visitor_to_update.email = verified_identity.email

                    db.add(
                        AuditEvent(
                            actor_type=ActorType.visitor,
                            actor_id=existing_session.visitor_id,
                            event_type="visitor.session.customer_authenticated",
                            resource_type="visitor_session",
                            resource_id=existing_session.id,
                            metadata_json={
                                "conversation_id": str(existing_session.conversation_id),
                                "request_id": request_id,
                                "customer_id": verified_identity.customer_id,
                                "ial": 2,
                            },
                        )
                    )
                    await db.commit()
                    return WidgetSessionResponse(
                        session_token=raw_token,
                        visitor_token=raw_token,
                        visitor_id=existing_session.visitor_id,
                        conversation_id=existing_session.conversation_id,
                        expires_at=existing_session.expires_at,
                        identity_assurance_level=2,
                        verified_customer_id=existing_session.verified_customer_id,
                    )

    # 5. Create new visitor, conversation, and session
    # If customer is verified and email wasn't provided, use verified email
    visitor_email = email
    if not visitor_email and verified_identity and verified_identity.email:
        visitor_email = verified_identity.email

    org_id = TenantContext.get_tenant_id()

    visitor = Visitor(
        organization_id=org_id,
        name=name,
        email=visitor_email,
        created_at=now,
        last_seen_at=now,
    )
    db.add(visitor)
    await db.flush()

    conversation = Conversation(
        organization_id=org_id,
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

    ial = 2 if verified_identity else 0
    verified_customer_id = verified_identity.customer_id if verified_identity else None
    customer_token_hash = verified_identity.token_hash if verified_identity else None
    customer_token_expires_at = verified_identity.expires_at if verified_identity else None

    session = VisitorSession(
        organization_id=org_id,
        visitor_id=visitor.id,
        conversation_id=conversation.id,
        token_hash=session_token_hash,
        origin=origin or configured_origin,
        expires_at=expires_at,
        created_at=now,
        verified_customer_id=verified_customer_id,
        customer_token_hash=customer_token_hash,
        identity_assurance_level=ial,
        customer_token_expires_at=customer_token_expires_at,
    )
    db.add(session)
    await db.flush()

    # Record audit event (never log raw tokens)
    audit_metadata: dict[str, Any] = {
        "conversation_id": str(conversation.id),
        "request_id": request_id,
        "ial": ial,
    }
    if verified_customer_id:
        audit_metadata["customer_id"] = verified_customer_id
        if customer_token_hash:
            audit_metadata["token_hash_prefix"] = customer_token_hash[:8]

    audit = AuditEvent(
        organization_id=org_id,
        actor_type=ActorType.visitor,
        actor_id=visitor.id,
        event_type="visitor.session.created",
        resource_type="visitor_session",
        resource_id=session.id,
        metadata_json=audit_metadata,
    )
    db.add(audit)
    await db.commit()

    return WidgetSessionResponse(
        session_token=raw_session_token,
        visitor_token=raw_session_token,
        visitor_id=visitor.id,
        conversation_id=conversation.id,
        expires_at=expires_at,
        identity_assurance_level=session.identity_assurance_level,
        verified_customer_id=session.verified_customer_id,
    )


async def invalidate_visitor_session(
    db: AsyncSession,
    session: VisitorSession,
    request_id: str | None = None,
) -> None:
    """Safely invalidate visitor session, close active conversation, and record audit event."""
    now = datetime.now(UTC)
    session.revoked_at = now

    conv_stmt = (
        select(Conversation)
        .where(Conversation.id == session.conversation_id)
        .with_for_update()
    )
    conv_res = await db.execute(conv_stmt)
    conv = conv_res.scalar_one_or_none()
    if conv and conv.status == ConversationStatus.open:
        conv.status = ConversationStatus.closed
        conv.updated_at = now

    audit = AuditEvent(
        actor_type=ActorType.visitor,
        actor_id=session.visitor_id,
        event_type="visitor.session.logged_out",
        resource_type="visitor_session",
        resource_id=session.id,
        metadata_json={
            "conversation_id": str(session.conversation_id),
            "request_id": request_id,
            "ial": session.effective_identity_assurance_level,
            "verified_customer_id": session.verified_customer_id,
        },
    )
    db.add(audit)
    await db.commit()


async def update_visitor_profile(
    db: AsyncSession,
    visitor_id: uuid.UUID,
    name: str | None = None,
    email: str | None = None,
    request_id: str | None = None,
) -> Visitor:
    """Update visitor name and/or email."""
    stmt = select(Visitor).where(Visitor.id == visitor_id).with_for_update()
    result = await db.execute(stmt)
    visitor = result.scalar_one_or_none()
    if not visitor:
        raise NotFoundError("Visitor not found")

    now = datetime.now(UTC)
    if name is not None:
        visitor.name = name.strip() if name.strip() else None
    if email is not None:
        visitor.email = email.strip() if email.strip() else None
    visitor.last_seen_at = now

    audit = AuditEvent(
        actor_type=ActorType.visitor,
        actor_id=visitor.id,
        event_type="visitor.profile.updated",
        resource_type="visitor",
        resource_id=visitor.id,
        metadata_json={"request_id": request_id},
    )
    db.add(audit)
    await db.commit()
    await db.refresh(visitor)
    return visitor
