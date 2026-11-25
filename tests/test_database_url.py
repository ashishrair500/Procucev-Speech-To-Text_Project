"""Tests for database URL normalisation (deployment hardening).

Managed Postgres providers hand out libpq URLs that the asyncpg driver cannot
consume as-is; these cases lock in the translation.
"""

from __future__ import annotations

from app.database import normalize_database_url


def test_bare_postgres_scheme_is_rewritten_for_asyncpg():
    url, args = normalize_database_url("postgres://u:p@host:5432/db")
    assert url.startswith("postgresql+asyncpg://")
    assert args == {}


def test_postgresql_scheme_is_rewritten_for_asyncpg():
    url, args = normalize_database_url("postgresql://u:p@host:5432/db")
    assert url.startswith("postgresql+asyncpg://")
    assert args == {}


def test_neon_sslmode_require_becomes_a_connect_arg():
    url, args = normalize_database_url(
        "postgresql://neondb_owner:pw@ep-cool-123.us-east-2.aws.neon.tech/neondb"
        "?sslmode=require"
    )
    assert "sslmode" not in url
    assert args["ssl"] is True


def test_sslmode_disable_does_not_enable_ssl():
    url, args = normalize_database_url("postgresql://u:p@host/db?sslmode=disable")
    assert "sslmode" not in url
    assert "ssl" not in args


def test_pooler_host_disables_prepared_statement_cache():
    _, args = normalize_database_url(
        "postgresql://u:p@ep-cool-123-pooler.us-east-2.aws.neon.tech/neondb"
        "?sslmode=require"
    )
    assert args["statement_cache_size"] == 0
    assert args["ssl"] is True


def test_unsupported_libpq_params_are_dropped():
    url, _ = normalize_database_url(
        "postgresql://u:p@host/db?channel_binding=require&target_session_attrs=any"
    )
    assert "channel_binding" not in url
    assert "target_session_attrs" not in url


def test_sqlite_urls_pass_through_untouched():
    url, args = normalize_database_url("sqlite+aiosqlite:///./local.db")
    assert url == "sqlite+aiosqlite:///./local.db"
    assert args == {}


def test_already_async_url_is_left_alone():
    url, args = normalize_database_url("postgresql+asyncpg://u:p@host:5432/db")
    assert url == "postgresql+asyncpg://u:p@host:5432/db"
    assert args == {}
