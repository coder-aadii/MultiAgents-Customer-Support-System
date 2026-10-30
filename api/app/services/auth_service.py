from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.errors import UnauthorizedError
from app.config import settings
from app.models.audit_event import ActorType, AuditEvent
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.schemas.auth import LoginRequest, RefreshResponse, TokenResponse
from app.schemas.user import UserResponse
from app.security.password import verify_password
from app.security.tokens import create_access_jwt, generate_opaque_token, hash_token


async def record_audit_event(
    db: AsyncSession,
    actor_type: ActorType,
    actor_id: uuid.UUID | None,
    event_type: str,
    resource_type: str,
    resource_id: uuid.UUID | None,
    metadata: dict[str, Any] | None = None,
) -> None:
    event = AuditEvent(
        actor_type=actor_type,
        actor_id=actor_id,
        event_type=event_type,
        resource_type=resource_type,
        resource_id=resource_id,
        metadata_json=metadata or {},
    )
    db.add(event)
    await db.flush()


async def login_user(
    db: AsyncSession,
    credentials: LoginRequest,
    request_id: str | None = None,
) -> tuple[TokenResponse, str]:
    """Authenticate staff user and return (TokenResponse, raw_refresh_token)."""
    normalized_email = credentials.email.strip().lower()

    stmt = select(User).where(User.email == normalized_email)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    # Indistinguishable failure check to prevent user enumeration
    valid_password = False
    if user:
        valid_password = verify_password(credentials.password, user.password_hash)
    else:
        # Dummy verification to balance timing
        verify_password(credentials.password, "$argon2id$m=65536,t=2,p=4$dummy$dummy")

    if not user or not valid_password or not user.is_active:
        await record_audit_event(
            db,
            actor_type=ActorType.system,
            actor_id=user.id if user else None,
            event_type="auth.login.failure",
            resource_type="user",
            resource_id=user.id if user else None,
            metadata={"email": normalized_email, "request_id": request_id},
        )
        await db.commit()
        raise UnauthorizedError("Invalid email or password")

    # Record login success
    await record_audit_event(
        db,
        actor_type=ActorType.user,
        actor_id=user.id,
        event_type="auth.login.success",
        resource_type="user",
        resource_id=user.id,
        metadata={"email": user.email, "request_id": request_id},
    )

    access_token = create_access_jwt(str(user.id), user.role.value)
    raw_refresh = generate_opaque_token()
    token_hash = hash_token(raw_refresh)

    now = datetime.now(UTC)
    refresh_record = RefreshToken(
        user_id=user.id,
        token_hash=token_hash,
        family_id=uuid.uuid4(),
        expires_at=now + timedelta(days=settings.JWT_REFRESH_DAYS),
    )
    db.add(refresh_record)
    await db.commit()

    token_response = TokenResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=settings.JWT_ACCESS_MINUTES * 60,
        user=UserResponse.model_validate(user),
    )
    return token_response, raw_refresh


async def rotate_refresh_token(
    db: AsyncSession,
    raw_token: str,
    request_id: str | None = None,
) -> tuple[RefreshResponse, str]:
    """Atomically rotate refresh token. Replay revokes family."""
    token_hash = hash_token(raw_token)
    now = datetime.now(UTC)

    stmt = select(RefreshToken).where(RefreshToken.token_hash == token_hash).with_for_update()
    result = await db.execute(stmt)
    token_record = result.scalar_one_or_none()

    if not token_record or token_record.expires_at <= now:
        raise UnauthorizedError("Invalid or expired refresh token")

    # Detect reuse: if token was already revoked, revoke its entire family!
    if token_record.revoked_at is not None:
        revoke_family_stmt = (
            update(RefreshToken)
            .where(RefreshToken.family_id == token_record.family_id)
            .values(revoked_at=now)
        )
        await db.execute(revoke_family_stmt)
        await record_audit_event(
            db,
            actor_type=ActorType.user,
            actor_id=token_record.user_id,
            event_type="auth.refresh.reuse_detected",
            resource_type="refresh_token",
            resource_id=token_record.id,
            metadata={"family_id": str(token_record.family_id), "request_id": request_id},
        )
        await db.commit()
        raise UnauthorizedError("Revoked token reuse detected; session terminated")

    # Verify user is active
    user_stmt = select(User).where(User.id == token_record.user_id)
    user_result = await db.execute(user_stmt)
    user = user_result.scalar_one_or_none()
    if not user or not user.is_active:
        raise UnauthorizedError("User account inactive")

    # Rotate token: insert new token first to satisfy foreign key constraint on replaced_by_id
    new_token_id = uuid.uuid4()
    new_raw = generate_opaque_token()
    new_record = RefreshToken(
        id=new_token_id,
        user_id=user.id,
        token_hash=hash_token(new_raw),
        family_id=token_record.family_id,
        expires_at=now + timedelta(days=settings.JWT_REFRESH_DAYS),
    )
    db.add(new_record)
    await db.flush()

    token_record.revoked_at = now
    token_record.replaced_by_id = new_token_id

    await record_audit_event(
        db,
        actor_type=ActorType.user,
        actor_id=user.id,
        event_type="auth.refresh.success",
        resource_type="refresh_token",
        resource_id=new_token_id,
        metadata={"request_id": request_id},
    )
    await db.commit()

    new_access = create_access_jwt(str(user.id), user.role.value)
    refresh_response = RefreshResponse(
        access_token=new_access,
        token_type="bearer",
        expires_in=settings.JWT_ACCESS_MINUTES * 60,
    )
    return refresh_response, new_raw


async def logout_user(
    db: AsyncSession,
    raw_token: str | None,
    current_user: User | None = None,
    request_id: str | None = None,
) -> None:
    """Revoke refresh token and record logout audit event."""
    if raw_token:
        token_hash = hash_token(raw_token)
        now = datetime.now(UTC)
        stmt = (
            update(RefreshToken)
            .where(RefreshToken.token_hash == token_hash, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        await db.execute(stmt)

    user_id = current_user.id if current_user else None
    await record_audit_event(
        db,
        actor_type=ActorType.user if user_id else ActorType.system,
        actor_id=user_id,
        event_type="auth.logout",
        resource_type="user",
        resource_id=user_id,
        metadata={"request_id": request_id},
    )
    await db.commit()
