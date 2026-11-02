from __future__ import annotations

from typing import Any

from fastapi import status
from fastapi.responses import JSONResponse

from app.schemas.common import ErrorCode


class AppException(Exception):
    def __init__(
        self,
        code: ErrorCode | str,
        message: str,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        details: dict[str, Any] | None = None,
    ):
        self.code = code if isinstance(code, str) else code.value
        self.message = message
        self.status_code = status_code
        self.details = details or {}
        super().__init__(self.message)


class ValidationError(AppException):
    def __init__(
        self, message: str = "Request validation failed", details: dict[str, Any] | None = None
    ):
        super().__init__(
            code=ErrorCode.VALIDATION_ERROR,
            message=message,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            details=details,
        )


class UnauthorizedError(AppException):
    def __init__(self, message: str = "Authentication required"):
        super().__init__(
            code=ErrorCode.UNAUTHORIZED,
            message=message,
            status_code=status.HTTP_401_UNAUTHORIZED,
        )


class ForbiddenError(AppException):
    def __init__(self, message: str = "Access forbidden"):
        super().__init__(
            code=ErrorCode.FORBIDDEN,
            message=message,
            status_code=status.HTTP_403_FORBIDDEN,
        )


class NotFoundError(AppException):
    def __init__(self, message: str = "Resource not found"):
        super().__init__(
            code=ErrorCode.NOT_FOUND,
            message=message,
            status_code=status.HTTP_404_NOT_FOUND,
        )


class RateLimitedError(AppException):
    def __init__(self, message: str = "Rate limit exceeded"):
        super().__init__(
            code=ErrorCode.RATE_LIMITED,
            message=message,
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )


class ConversationClosedError(AppException):
    def __init__(self, message: str = "Conversation is closed"):
        super().__init__(
            code=ErrorCode.CONVERSATION_CLOSED,
            message=message,
            status_code=status.HTTP_409_CONFLICT,
        )


class ConflictError(AppException):
    def __init__(self, message: str = "Resource conflict"):
        super().__init__(
            code="conflict",
            message=message,
            status_code=status.HTTP_409_CONFLICT,
        )


class BadRequestError(AppException):
    def __init__(self, message: str = "Bad request"):
        super().__init__(
            code="bad_request",
            message=message,
            status_code=status.HTTP_400_BAD_REQUEST,
        )


class InternalError(AppException):
    def __init__(self, message: str = "An unexpected error occurred"):
        super().__init__(
            code=ErrorCode.INTERNAL_ERROR,
            message=message,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


def format_error_response(
    code: str,
    message: str,
    status_code: int,
    details: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> JSONResponse:
    content: dict[str, Any] = {
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
        }
    }
    if request_id:
        content["error"]["request_id"] = request_id

    headers = {}
    if request_id:
        headers["X-Request-ID"] = request_id

    return JSONResponse(status_code=status_code, content=content, headers=headers)
