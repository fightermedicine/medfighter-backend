"""Async SQLAlchemy engine/session (§12, §42)."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        db_url = get_settings().database_url
        connect_args = {}
        if "asyncpg" in db_url:
            connect_args["statement_cache_size"] = 0
            connect_args["prepared_statement_cache_size"] = 0
        engine_kwargs: dict = {
            "pool_pre_ping": False,
            "connect_args": connect_args,
        }
        if "sqlite" not in db_url:
            engine_kwargs["pool_size"] = 5
            engine_kwargs["max_overflow"] = 10
            engine_kwargs["pool_timeout"] = 15
            engine_kwargs["pool_recycle"] = 180

        _engine = create_async_engine(
            db_url,
            **engine_kwargs,
        )
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _sessionmaker


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a transactional session."""
    async with get_sessionmaker()() as session:
        yield session


def reset_engine() -> None:
    """Drop cached engine/sessionmaker (used by tests to rebind settings)."""
    global _engine, _sessionmaker
    _engine = None
    _sessionmaker = None
