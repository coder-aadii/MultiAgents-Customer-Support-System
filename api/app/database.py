from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text
from app.config import settings

# Import all models to register them with Base
from app.models import (  # noqa: F401
    User,
    WidgetSettings,
    Visitor,
    Conversation,
    Message,
    Handoff,
    KnowledgeDocument,
    KnowledgeChunk,
    AuditEvent,
)

from app.models.base import Base


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


async def get_session() -> AsyncSession:
    async with async_session_factory() as session:
        yield session


async def check_database_connection() -> bool:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def init_db() -> None:
    # Database tables are created via Alembic migrations, not create_all
    # This function exists for compatibility but does nothing
    pass


async def close_db() -> None:
    await engine.dispose()