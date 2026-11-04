"""Pytest configuration for API tests."""

import os
import re

# Force test environment variables before importing app
os.environ["MCSS_ENV"] = "test"
os.environ["MCSS_LLM_BASE_URL"] = "fake"

# Force tests to run against an isolated test database (mcss_test) to protect development data
_base_db_url = os.environ.get("MCSS_DATABASE_URL", "postgresql+psycopg://mcss:change-me@postgres:5432/mcss")
if "/mcss_test" not in _base_db_url:
    _test_db_url = re.sub(r"/mcss(?:\?[^/]*)?$", "/mcss_test", _base_db_url)
    if _test_db_url == _base_db_url:
        _test_db_url = _base_db_url.rsplit("/", 1)[0] + "/mcss_test"
    os.environ["MCSS_DATABASE_URL"] = _test_db_url
else:
    _test_db_url = _base_db_url

os.environ.setdefault("MCSS_JWT_SECRET", "test-secret-key-at-least-32-chars-long")
os.environ.setdefault("MCSS_ADMIN_EMAIL", "admin@example.com")
os.environ.setdefault("MCSS_ADMIN_PASSWORD", "testpassword123")

import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402

settings.MCSS_ENV = "test"
settings.MCSS_LLM_BASE_URL = "fake"


def pytest_sessionstart(session):
    """Ensure the isolated test database exists and is migrated to head."""
    import psycopg
    from alembic import command
    from alembic.config import Config

    test_db_url = os.environ.get("MCSS_DATABASE_URL", "")
    match = re.search(r"://([^:]+):([^@]+)@([^:/]+)(?::(\d+))?/(.+)", test_db_url)
    if match:
        user, password, host, port, db_name = match.groups()
        port = int(port) if port else 5432
        try:
            with psycopg.connect(
                dbname="postgres",
                user=user,
                password=password,
                host=host,
                port=port,
                autocommit=True,
            ) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
                    if not cur.fetchone():
                        cur.execute(f'CREATE DATABASE "{db_name}"')
        except Exception:
            pass

    try:
        cfg = Config("alembic.ini")
        cfg.set_main_option("script_location", "alembic")
        cfg.set_main_option("sqlalchemy.url", test_db_url)
        command.upgrade(cfg, "head")
    except Exception:
        pass


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
