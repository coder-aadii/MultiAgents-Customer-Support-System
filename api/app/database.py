from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

# Import all models to register them with Base
from app.models import (  # noqa: F401
    AssistantJob,
    AuditEvent,
    Conversation,
    EvidenceRecord,
    Handoff,
    KnowledgeChunk,
    KnowledgeDocument,
    Message,
    RefreshToken,
    User,
    Visitor,
    VisitorSession,
    WorkflowCheckpoint,
    WidgetSettings,
)

EXPECTED_MIGRATION_REVISION = "0019_organization_entity_schemas"

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.MCSS_ENV == "development",
    pool_pre_ping=True,
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        yield session


async def check_database_connection() -> bool:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            result = await conn.execute(text("SELECT version_num FROM alembic_version LIMIT 1"))
            row = result.fetchone()
            if not row or row[0] != EXPECTED_MIGRATION_REVISION:
                return False
        return True
    except Exception:
        return False


async def init_db() -> None:
    # Database tables are created via Alembic migrations, not create_all
    # This function exists for compatibility but does nothing
    pass


async def close_db() -> None:
    await engine.dispose()
