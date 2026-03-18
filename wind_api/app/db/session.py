"""
Async database session management.

Provides the SQLAlchemy async engine, session factory, and the FastAPI
dependency `get_db` that injects a database session into route handlers.

Production usage:
    @router.post("/calculate/wind/qz")
    async def calc_qz(payload: ..., db: AsyncSession = Depends(get_db)):
        terrain = await repository.fetch_terrain_constants(db, code_version, exposure)
        ...
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings


# ---------------------------------------------------------------------------
# Engine & Session Factory
# ---------------------------------------------------------------------------
# Created once at application startup. The engine manages the connection pool;
# the session_factory produces individual sessions per request.
# ---------------------------------------------------------------------------

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def init_engine() -> AsyncEngine:
    """
    Create the async SQLAlchemy engine.

    Called once during FastAPI lifespan startup. Uses the connection pool
    parameters from Settings.
    """
    global _engine, _session_factory
    settings = get_settings()

    _engine = create_async_engine(
        settings.DATABASE_URL,
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=settings.DB_MAX_OVERFLOW,
        echo=settings.DB_ECHO_SQL,
        # Prevent connection leaks in async context
        pool_pre_ping=True,
    )

    _session_factory = async_sessionmaker(
        bind=_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    return _engine


async def dispose_engine() -> None:
    """Gracefully close all connections. Called during FastAPI shutdown."""
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None


# ---------------------------------------------------------------------------
# FastAPI Dependency — inject into any route handler
# ---------------------------------------------------------------------------

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that yields an async database session.

    Usage in a route:
        async def my_endpoint(db: AsyncSession = Depends(get_db)):
            result = await db.execute(select(TerrainExposure).where(...))

    The session is automatically committed on success and rolled back on
    exception, then closed when the request finishes.
    """
    if _session_factory is None:
        raise RuntimeError(
            "Database not initialized. Ensure init_engine() was called "
            "during application startup."
        )

    async with _session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
