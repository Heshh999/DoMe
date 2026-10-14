from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine


def make_engine(database_url: str, *, pool_size: int = 10, max_overflow: int = 20) -> AsyncEngine:
    return create_async_engine(
        database_url,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_pre_ping=True,
        # Fly.io Managed Postgres guidance: recycle client connections every 600 s; the Fly proxy drains a
        # connection after 10 minutes, and pre-ping catches one that was closed in between.
        pool_recycle=600,
    )


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


@asynccontextmanager
async def transaction(factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    """One session, one transaction: commits on success, rolls back on any exception."""
    async with factory() as session:
        async with session.begin():
            yield session
