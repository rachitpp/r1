"""asyncpg connection-pool helpers.

Thin create/close wrappers used by the FastAPI lifespan. Business logic and
queries live elsewhere; this module only owns pool lifecycle.
"""

from __future__ import annotations

import asyncpg


async def create_pool(dsn: str) -> asyncpg.Pool:
    """Create and return an asyncpg pool for ``dsn``.

    Raises whatever asyncpg raises on failure; callers decide how to tolerate
    it (the API lifespan logs and continues so ``/health`` stays serviceable).
    """
    return await asyncpg.create_pool(dsn)


async def close_pool(pool: asyncpg.Pool | None) -> None:
    """Close ``pool`` if it exists."""
    if pool is not None:
        await pool.close()
