from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.errors import UnauthorizedError
from app.database import get_session
from app.dependencies import get_current_user
from app.models.user import User
from app.schemas.auth import LoginRequest, RefreshResponse, TokenResponse
from app.schemas.user import UserResponse
from app.security.cookies import (
    clear_refresh_cookie,
    get_refresh_token_from_request,
    set_refresh_cookie,
)
from app.services.auth_service import login_user, logout_user, rotate_refresh_token

auth_router = APIRouter(prefix="/auth", tags=["auth"])


@auth_router.post("/login", response_model=TokenResponse)
async def login(
    request: Request,
    response: Response,
    credentials: LoginRequest,
    db: AsyncSession = Depends(get_session),
) -> TokenResponse:
    request_id = getattr(request.state, "request_id", None)
    token_response, raw_refresh = await login_user(db, credentials, request_id=request_id)
    set_refresh_cookie(response, raw_refresh)
    return token_response


@auth_router.post("/refresh", response_model=RefreshResponse)
async def refresh(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_session),
) -> RefreshResponse:
    request_id = getattr(request.state, "request_id", None)
    raw_token = get_refresh_token_from_request(request)
    if not raw_token:
        raise UnauthorizedError("Refresh token missing")

    refresh_response, new_raw_refresh = await rotate_refresh_token(
        db, raw_token, request_id=request_id
    )
    set_refresh_cookie(response, new_raw_refresh)
    return refresh_response


@auth_router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    request_id = getattr(request.state, "request_id", None)
    raw_token = get_refresh_token_from_request(request)
    await logout_user(db, raw_token, request_id=request_id)
    clear_refresh_cookie(response)
    return {"status": "ok"}


@auth_router.get("/me", response_model=UserResponse)
async def get_me(
    current_user: User = Depends(get_current_user),
) -> UserResponse:
    return UserResponse.model_validate(current_user)
