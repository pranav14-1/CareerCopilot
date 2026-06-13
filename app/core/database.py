import logging
from typing import AsyncGenerator
from redis.asyncio import Redis, from_url
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings

logger = logging.getLogger(__name__)

# --- SQLAlchemy Async Engine and Session Setup ---
async_engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency generator for obtaining an asynchronous SQLAlchemy session.
    Automatically handles rollback on exceptions and final close.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# --- Redis Client Setup ---
redis_client: Redis = from_url(settings.REDIS_URL, decode_responses=True)


async def get_redis() -> AsyncGenerator[Redis, None]:
    """Dependency generator for obtaining an asynchronous Redis connection."""
    yield redis_client


# --- Connection Verification Helpers ---
async def verify_database_connection() -> bool:
    """Verifies connection to PostgreSQL by running a simple query."""
    from sqlalchemy import text
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        logger.info("Successfully connected to the PostgreSQL database.")
        return True
    except Exception as e:
        logger.error(f"Failed to connect to the database: {e}")
        return False


async def verify_redis_connection() -> bool:
    """Verifies connection to Redis by running ping."""
    try:
        await redis_client.ping()
        logger.info("Successfully connected to the Redis cache.")
        return True
    except Exception as e:
        logger.error(f"Failed to connect to Redis: {e}")
        return False


async def init_db() -> None:
    """
    Creates all database tables defined in the declarative schema.
    Also ensures the pgvector extension is created and migrates necessary fields.
    """
    from app.models.schemas import Base
    from sqlalchemy import text
    try:
        async with async_engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.run_sync(Base.metadata.create_all)
            # Add url column if it doesn't exist to support deduplication
            await conn.execute(text("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS url VARCHAR(512)"))
        logger.info("Database tables successfully initialized with pgvector and schema migrations.")
    except Exception as e:
        logger.error(f"Failed to initialize database tables: {e}")
        raise

