"""Pytest configuration for API tests."""

import os

# Force test environment variables before importing app
os.environ["MCSS_ENV"] = "test"
os.environ["MCSS_LLM_BASE_URL"] = "fake"
os.environ.setdefault("MCSS_DATABASE_URL", "postgresql+psycopg://mcss:change-me@postgres:5432/mcss")
os.environ.setdefault("MCSS_JWT_SECRET", "test-secret-key-at-least-32-chars-long")
os.environ.setdefault("MCSS_ADMIN_EMAIL", "admin@example.com")
os.environ.setdefault("MCSS_ADMIN_PASSWORD", "testpassword123")

import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402

settings.MCSS_ENV = "test"
settings.MCSS_LLM_BASE_URL = "fake"


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
