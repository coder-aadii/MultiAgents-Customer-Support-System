from __future__ import annotations

from fastapi import Request, Response

from app.config import settings

REFRESH_COOKIE_NAME = "mcss_refresh_token"
AUTH_COOKIE_PATH = "/api/v1/auth"


def set_refresh_cookie(response: Response, raw_refresh_token: str) -> None:
    """Set HttpOnly, secure path-scoped cookie for refresh token."""
    is_prod = settings.MCSS_ENV == "production"
    max_age_seconds = settings.JWT_REFRESH_DAYS * 86400

    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=raw_refresh_token,
        max_age=max_age_seconds,
        httponly=True,
        secure=is_prod,
        samesite="strict" if is_prod else "lax",
        path=AUTH_COOKIE_PATH,
    )


def clear_refresh_cookie(response: Response) -> None:
    """Clear the refresh token cookie."""
    is_prod = settings.MCSS_ENV == "production"
    response.delete_cookie(
        key=REFRESH_COOKIE_NAME,
        path=AUTH_COOKIE_PATH,
        httponly=True,
        secure=is_prod,
        samesite="strict" if is_prod else "lax",
    )


def get_refresh_token_from_request(request: Request) -> str | None:
    """Extract refresh token from cookie or header."""
    cookie_token = request.cookies.get(REFRESH_COOKIE_NAME)
    if cookie_token:
        return cookie_token

    auth_header = request.headers.get("X-Refresh-Token")
    if auth_header:
        return auth_header

    return None
