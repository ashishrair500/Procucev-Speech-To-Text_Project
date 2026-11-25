"""Async SQLAlchemy engine, session factory and declarative base."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    AsyncEngine,
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
    * asyncpg rejects the libpq ``sslmode`` and ``channel_binding`` parameters.

    Neon's PgBouncer pooler runs in transaction mode and cannot use asyncpg's
    prepared-statement cache, so the cache is disabled for pooler hostnames.
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


def pick_migration_url(pooled: str, unpooled: str | None) -> str:
    """Choose the URL used for DDL — the direct endpoint when we have one.

    Neon's pooled connection routes through PgBouncer in transaction mode, so
    schema operations (``CREATE TABLE``, Alembic, ``pg_dump``) belong on the
    direct endpoint. Falls back to the pooled URL when no direct one is set.
    """
    return unpooled or pooled


def _build_engine(url: str) -> AsyncEngine:
    normalized, connect_args = normalize_database_url(url)
    return create_async_engine(
        normalized,
        echo=False,
        pool_pre_ping=True,
        future=True,
        connect_args=connect_args,
    )


# Application query traffic.
engine = _build_engine(settings.database_url)

# Schema DDL. Reuse the application engine when both URLs are the same object
# so we do not open a second pool for the common local/SQLite case.
_migration_url = pick_migration_url(settings.database_url, settings.database_url_unpooled)
migration_engine = engine if _migration_url == settings.database_url else _build_engine(_migration_url)

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
    """Create tables on startup (Alembic can replace this later).

    Runs on the direct endpoint so it works with a pooled Neon URL.
    """
    # Import models so they are registered on Base.metadata before create_all.
    from app import models  # noqa: F401

    async with migration_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def dispose_engines() -> None:
    await migration_engine.dispose()
    if migration_engine is not engine:
        await engine.dispose()
