"""asyncpg connection-pool helpers.

Thin create/close wrappers used by the FastAPI lifespan and the ingest CLI.
Business logic and queries live elsewhere; this module only owns pool
lifecycle and the per-connection type setup pgvector needs.
"""

from __future__ import annotations

import asyncpg
from pgvector.asyncpg import register_vector


async def _init_connection(conn: asyncpg.Connection) -> None:
    """Register the pgvector codec so ``vector`` columns round-trip as lists."""
    await register_vector(conn)


async def create_pool(dsn: str) -> asyncpg.Pool:
    """Create and return an asyncpg pool for ``dsn``.

    Every pooled connection registers the pgvector codec on open, so callers
    can pass Python ``list[float]`` for ``vector`` columns and read them back
    the same way. Raises whatever asyncpg raises on failure; callers decide how
    to tolerate it (the API lifespan logs and continues so ``/health`` stays
    serviceable).
    """
    return await asyncpg.create_pool(dsn, init=_init_connection)


async def close_pool(pool: asyncpg.Pool | None) -> None:
    """Close ``pool`` if it exists."""
    if pool is not None:
        await pool.close()
