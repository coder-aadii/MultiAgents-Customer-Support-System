import asyncio
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from app.api.router import api_v1_router, headless_router
from app.common.errors import AppException, format_error_response
from app.config import settings
from app.database import check_database_connection, close_db
from app.services.knowledge_worker import run_ingestion_worker_cycle

structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(20),
)

logger = structlog.get_logger()


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path in ("/docs", "/redoc"):
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                "img-src 'self' data: https://fastapi.tiangolo.com; "
                "font-src 'self' https://cdn.jsdelivr.net; "
                "connect-src 'self'; frame-ancestors 'none';"
            )
        else:
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none';"
            )
        return response


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings.validate_production()
    logger.info("application_starting", env=settings.MCSS_ENV)
    # Recover and drain any lingering or abandoned ingestion jobs from previous crashes/restarts
    asyncio.create_task(run_ingestion_worker_cycle(worker_id="startup_recovery_worker"))
    yield
    await close_db()
    logger.info("application_shutdown")


app = FastAPI(
    title="MCSS API",
    description="Multi-Agent Customer Support System",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.MCSS_ENV != "production" else None,
    redoc_url="/redoc" if settings.MCSS_ENV != "production" else None,
)


@app.middleware("http")
async def add_request_id(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    request.state.request_id = request_id

    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    logger.warning("app_exception", request_id=request_id, code=exc.code, message=exc.message)
    return format_error_response(
        code=exc.code,
        message=exc.message,
        status_code=exc.status_code,
        details=exc.details,
        request_id=request_id,
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    code_map = {
        400: "VALIDATION_ERROR",
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        405: "METHOD_NOT_ALLOWED",
        409: "CONFLICT",
        422: "VALIDATION_ERROR",
        429: "RATE_LIMITED",
    }
    code = code_map.get(exc.status_code, "HTTP_ERROR")
    logger.warning(
        "http_exception", request_id=request_id, status_code=exc.status_code, detail=exc.detail
    )
    return format_error_response(
        code=code,
        message=str(exc.detail),
        status_code=exc.status_code,
        request_id=request_id,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    raw_errors = jsonable_encoder(exc.errors())
    logger.warning("validation_error", request_id=request_id, errors=raw_errors)
    return format_error_response(
        code="VALIDATION_ERROR",
        message="Request validation failed",
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        details={"validation_errors": raw_errors},
        request_id=request_id,
    )


@app.exception_handler(ValidationError)
async def pydantic_validation_exception_handler(
    request: Request, exc: ValidationError
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    raw_errors = jsonable_encoder(exc.errors())
    logger.warning("pydantic_validation_error", request_id=request_id, errors=raw_errors)
    return format_error_response(
        code="VALIDATION_ERROR",
        message="Data validation failed",
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        details={"validation_errors": raw_errors},
        request_id=request_id,
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    logger.error("unhandled_exception", request_id=request_id, error=str(exc), exc_info=True)
    return format_error_response(
        code="INTERNAL_ERROR",
        message="An unexpected error occurred",
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        request_id=request_id,
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

app.add_middleware(SecurityHeadersMiddleware)

app.include_router(api_v1_router)
app.include_router(headless_router)


@app.get("/healthz", tags=["health"])
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz", tags=["health"])
async def readyz() -> Response:
    is_ready = await check_database_connection()
    if not is_ready:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "not ready", "reason": "database unavailable"},
        )
    return JSONResponse(status_code=status.HTTP_200_OK, content={"status": "ok"})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.MCSS_API_HOST,
        port=settings.MCSS_API_PORT,
        reload=settings.MCSS_ENV == "development",
    )
