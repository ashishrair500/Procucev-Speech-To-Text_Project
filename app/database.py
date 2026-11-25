"""Async SQLAlchemy engine, session factory and declarative base."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


def normalize_database_url(url: str) -> tuple[str, dict]:
    """Coerce a provider-supplied URL into a form the asyncpg driver accepts.

    Managed Postgres providers (Neon, Supabase, Render) hand out libpq-style
    URLs such as ``postgresql://user:pw@host/db?sslmode=require``. Two things
    break the async driver:

    * the bare ``postgresql://`` scheme selects psycopg2, not asyncpg;
    * asyncpg rejects the libpq ``sslmode`` parameter outright.

    Neon's PgBouncer pooler additionally cannot use asyncpg's prepared-statement
    cache, so the cache is disabled for pooler hostnames.
    """
    connect_args: dict = {}

    if url.startswith("postgres://"):
        url = "postgresql+asyncpg://" + url[len("postgres://") :]
    elif url.startswith("postgresql://"):
        url = "postgresql+asyncpg://" + url[len("postgresql://") :]

    # Only Postgres URLs need surgery; leave sqlite:/// and friends untouched
    # (urlunsplit would collapse their triple slash).
    if not url.startswith("postgresql+asyncpg://"):
        return url, connect_args

    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))

    sslmode = query.pop("sslmode", None)
    if sslmode and sslmode != "disable":
        connect_args["ssl"] = True

    # Parameters libpq understands but asyncpg does not.
    for unsupported in ("channel_binding", "target_session_attrs", "options"):
        query.pop(unsupported, None)

    if "-pooler" in parts.netloc:
        connect_args["statement_cache_size"] = 0

    normalized = urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )
    return normalized, connect_args


_database_url, _connect_args = normalize_database_url(settings.database_url)

engine = create_async_engine(
    _database_url,
    echo=False,
    pool_pre_ping=True,
    future=True,
    connect_args=_connect_args,
)

SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding a request-scoped session."""
    async with SessionLocal() as session:
        yield session


async def init_models() -> None:
    """Create tables on startup (Alembic can replace this later)."""
    # Import models so they are registered on Base.metadata before create_all.
    from app import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
