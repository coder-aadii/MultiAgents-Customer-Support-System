from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from app.common.errors import UnauthorizedError
from app.config import settings


def hash_token(raw_token: str) -> str:
    """Generate SHA-256 hex digest of an opaque token for database storage."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def generate_opaque_token() -> str:
    """Generate a cryptographically random, URL-safe opaque token."""
    return secrets.token_urlsafe(32)


def create_access_jwt(
    subject: str,
    role: str,
    additional_claims: dict[str, Any] | None = None,
    org_id: str | None = None,
    **kwargs: Any,
) -> str:
    """Create short-lived access JWT containing sub, role, iat, exp, jti, and optional claims."""
    now = datetime.now(UTC)
    expires_at = now + timedelta(minutes=settings.JWT_ACCESS_MINUTES)

    payload = {
        "sub": subject,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "jti": str(uuid.uuid4()),
    }
    if org_id:
        payload["org_id"] = str(org_id)
    if additional_claims:
        payload.update(additional_claims)
    if kwargs:
        payload.update(kwargs)

    return jwt.encode(payload, settings.JWT_SECRET, algorithm="HS256")


def decode_access_jwt(token: str) -> dict[str, Any]:
    """Decode and validate access JWT signature and expiry.

    Raises UnauthorizedError on invalid/expired tokens.
    """
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=["HS256"],
            options={"require": ["sub", "role", "iat", "exp", "jti"]},
        )
        return payload
    except jwt.ExpiredSignatureError as e:
        raise UnauthorizedError("Access token has expired") from e
    except jwt.PyJWTError as e:
        raise UnauthorizedError("Invalid access token") from e
