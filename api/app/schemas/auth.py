from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator

from app.schemas.user import UserResponse

EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        normalized = v.strip().lower()
        if not EMAIL_REGEX.match(normalized):
            raise ValueError("Invalid email address format")
        return normalized


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserResponse


class RefreshResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
