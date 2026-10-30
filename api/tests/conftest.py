"""Pytest configuration for API tests."""

import os

# Set test environment before importing app
os.environ.setdefault("MCSS_ENV", "test")
os.environ.setdefault("MCSS_LLM_BASE_URL", "fake")
os.environ.setdefault(
    "MCSS_DATABASE_URL", "postgresql+psycopg://mcss:change-me@postgres:5432/mcss"
)
os.environ.setdefault("MCSS_JWT_SECRET", "test-secret-key-at-least-32-chars-long")
os.environ.setdefault("MCSS_ADMIN_EMAIL", "admin@example.com")
os.environ.setdefault("MCSS_ADMIN_PASSWORD", "testpassword123")

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from app.main import app


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
