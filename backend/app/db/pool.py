"""asyncpg connection-pool helpers.

Thin create/close wrappers used by the FastAPI lifespan, the worker, and the
CLIs. Business logic and queries live elsewhere; this module owns pool
lifecycle, the per-connection type setup pgvector needs, and the one rule that
keeps the pool from becoming the bottleneck: **hold a connection for the work,
not for the request**.

:func:`acquire` is how that rule is expressed. It takes either a pool or an
already-open connection and yields a connection for the duration of a block, so
a caller that owns one connection (the CLIs, the tests) and a caller that should
borrow one per operation (the API's agent loop) can share the same code path
without either of them special-casing the other.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
from pgvector.asyncpg import register_vector

from app import metrics
from app.config import get_settings

# Either end of the "where does a connection come from" question. A pool means
# short checkouts; a bare connection means the caller already scoped one.
ConnSource = Any


async def _init_connection(conn: asyncpg.Connection) -> None:
    """Register the pgvector codec so ``vector`` columns round-trip as lists."""
    await register_vector(conn)


async def create_pool(
    dsn: str,
    *,
    min_size: int | None = None,
    max_size: int | None = None,
    command_timeout: float | None = None,
    max_inactive_connection_lifetime: float | None = None,
) -> asyncpg.Pool:
    """Create and return an asyncpg pool for ``dsn``.

    Every pooled connection registers the pgvector codec on open, so callers
    can pass Python ``list[float]`` for ``vector`` columns and read them back
    the same way. Raises whatever asyncpg raises on failure; callers decide how
    to tolerate it (the API lifespan logs and continues so ``/health`` stays
    serviceable).

    Sizing defaults come from settings rather than from asyncpg, whose own
    default is min=max=10 — a number nobody chose, which is exactly the problem
    with it. ``command_timeout`` bounds a single statement: the API wants one
    (a statement that runs long is holding a pooled connection hostage), the
    ingest worker passes ``None`` because a batch insert of a few thousand
    embeddings legitimately takes a while.
    """
    settings = get_settings()
    return await asyncpg.create_pool(
        dsn,
        init=_init_connection,
        min_size=settings.DB_POOL_MIN_SIZE if min_size is None else min_size,
        max_size=settings.DB_POOL_MAX_SIZE if max_size is None else max_size,
        command_timeout=(
            settings.DB_COMMAND_TIMEOUT_S if command_timeout is None else command_timeout
        ),
        max_inactive_connection_lifetime=(
            settings.DB_POOL_MAX_IDLE_S
            if max_inactive_connection_lifetime is None
            else max_inactive_connection_lifetime
        ),
    )


async def close_pool(pool: asyncpg.Pool | None) -> None:
    """Close ``pool`` if it exists."""
    if pool is not None:
        await pool.close()


@asynccontextmanager
async def acquire(source: ConnSource) -> AsyncIterator[asyncpg.Connection]:
    """Yield a connection from ``source`` for the duration of the block.

    A pool is checked out and released here — which is the point: the SSE chat
    endpoint runs for as long as an agent loop takes, and a connection pinned
    for that whole span is a connection nobody else can have. With this, a
    six-tool-call answer borrows a connection six times for milliseconds each
    instead of once for two minutes.

    A bare connection is yielded unchanged, so the ingest CLI, the agent CLI,
    and the fake connection in the tests keep working without knowing a pool
    exists.

    Wait time is measured, not the checkout: the number that matters is how long
    a caller sat in the queue, because that is the one that goes non-zero before
    anything else looks wrong.
    """
    if not isinstance(source, asyncpg.Pool):
        yield source
        return
    started = time.perf_counter()
    async with source.acquire() as conn:
        metrics.db_pool_acquire_wait.observe(time.perf_counter() - started)
        yield conn


def sample_pool_gauges(pool: ConnSource | None) -> None:
    """Publish current pool occupancy to the metrics registry.

    Called at scrape time rather than continuously — occupancy is a level, and
    a level only needs to be read when someone is looking.

    Anything that is not a real pool is skipped rather than probed. ``/metrics``
    must not be the endpoint that falls over because something else was wired
    into ``app.state.pool``.
    """
    if not isinstance(pool, asyncpg.Pool):
        return
    metrics.db_pool_size.set(pool.get_size())
    metrics.db_pool_idle.set(pool.get_idle_size())
