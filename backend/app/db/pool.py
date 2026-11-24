"""asyncpg connection pool lifecycle + schema migration."""

from __future__ import annotations

import json
from pathlib import Path

import asyncpg
import structlog

from app.config import Settings

log = structlog.get_logger(__name__)

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


async def _init_connection(conn: asyncpg.Connection) -> None:
    # Return JSONB columns as Python objects.
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


async def create_pool(settings: Settings) -> asyncpg.Pool:
    pool = await asyncpg.create_pool(
        dsn=settings.database_url,
        min_size=settings.db_pool_min,
        max_size=settings.db_pool_max,
        statement_cache_size=settings.db_statement_cache_size,
        command_timeout=30,
        init=_init_connection,
    )
    assert pool is not None
    return pool


async def migrate(pool: asyncpg.Pool, settings: Settings) -> None:
    sql = _SCHEMA_PATH.read_text(encoding="utf-8").replace("{{EMBEDDING_DIM}}", str(settings.embedding_dim))
    async with pool.acquire() as conn:
        # Serialise concurrent migrations from multiple instances.
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(7426001)")
            await conn.execute(sql)
    log.info("db.migrated")
