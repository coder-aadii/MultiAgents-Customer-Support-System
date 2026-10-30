from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.errors import ForbiddenError, NotFoundError, UnauthorizedError
from app.database import get_session
from app.models.user import User, UserRole
from app.models.visitor_session import VisitorSession
from app.security.tokens import decode_access_jwt, hash_token


async def get_current_user(
    request: Request,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_session),
) -> User:
    """Validate access JWT and return active User instance.

    Rejects deactivated users even with a valid JWT.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise UnauthorizedError("Authentication token required")

    raw_token = authorization.split(" ", 1)[1].strip()
    payload = decode_access_jwt(raw_token)

    try:
        user_id = uuid.UUID(payload["sub"])
    except (ValueError, KeyError, TypeError) as e:
        raise UnauthorizedError("Malformed token subject") from e

    stmt = select(User).where(User.id == user_id)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user:
        raise UnauthorizedError("User does not exist")

    if not user.is_active:
        raise UnauthorizedError("Account is inactive")

    return user


async def require_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    """Require authenticated user to have admin role."""
    if current_user.role != UserRole.admin:
        raise ForbiddenError("Admin privileges required")
    return current_user


async def require_staff(
    current_user: User = Depends(get_current_user),
) -> User:
    """Require authenticated user to have staff or admin role."""
    if current_user.role not in (UserRole.admin, UserRole.staff):
        raise ForbiddenError("Staff access required")
    return current_user


async def get_visitor_session(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_session),
) -> VisitorSession:
    """Authenticate presented visitor bearer session token."""
    if not authorization or not authorization.startswith("Bearer "):
        raise UnauthorizedError("Valid visitor session required")

    raw_token = authorization.split(" ", 1)[1].strip()
    token_hash = hash_token(raw_token)
    now = datetime.now(UTC)

    stmt = select(VisitorSession).where(
        VisitorSession.token_hash == token_hash,
        VisitorSession.revoked_at.is_(None),
        VisitorSession.expires_at > now,
    )
    result = await db.execute(stmt)
    session = result.scalar_one_or_none()

    if not session:
        raise UnauthorizedError("Session expired or invalid")

    return session


async def validate_visitor_conversation(
    conversation_id: uuid.UUID,
    session: VisitorSession = Depends(get_visitor_session),
) -> VisitorSession:
    """Ensure visitor session is authorized for target conversation.

    Returns 404 (non-discoverable) on mismatch.
    """
    if session.conversation_id != conversation_id:
        raise NotFoundError("Resource not found")
    return session
