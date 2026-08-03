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

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
from pgvector.asyncpg import register_vector

from app import metrics
from app.config import get_settings

logger = logging.getLogger(__name__)

# Bounds the liveness probe. Deliberately short: this runs before every pooled
# checkout, so a slow probe is a tax on every query, and a probe that has not
# answered in a second is itself the evidence the connection is no good.
PING_TIMEOUT_S = 1.0
# Releasing a connection known to be dead must not block on it. asyncpg would
# otherwise wait to reset a session that is never going to reply.
DEAD_RELEASE_TIMEOUT_S = 1.0

# When the pool was last known to be talking to the server. Module-level
# because it is a property of the process's connection to the database, not
# of any one pool object — and because `PoolConnectionProxy` allows neither
# weak references nor attributes, so per-connection tracking is not available
# without reaching into asyncpg internals.
_last_activity: float = 0.0

# Either end of the "where does a connection come from" question. A pool means
# short checkouts; a bare connection means the caller already scoped one.
ConnSource = Any


async def _init_connection(conn: asyncpg.Connection) -> None:
    """Register the pgvector codec so ``vector`` columns round-trip as lists."""
    await register_vector(conn)


async def _is_live(conn: asyncpg.Connection) -> bool:
    """Whether ``conn`` can still talk to the server.

    A real round trip, because that is the only test that works: a connection
    the server has dropped still reports ``is_closed() is False`` on this side
    until something tries to write to it. `SELECT 1` is the cheapest way to be
    that something.

    The timeout matters as much as the query. Without it a connection to a
    server that has gone away — as opposed to one that closed politely — hangs
    here instead of failing, which turns a fast retry into the stall it was
    meant to prevent.
    """
    if conn.is_closed():
        return False
    try:
        await conn.fetchval("SELECT 1", timeout=PING_TIMEOUT_S)
    except (
        asyncpg.PostgresConnectionError,
        asyncpg.InterfaceError,
        ConnectionError,
        OSError,
        TimeoutError,
    ):
        return False
    return True


async def _discard(conn: asyncpg.Connection) -> None:
    """Close a dead connection so the pool replaces rather than re-lends it.

    Best-effort by definition: this is called precisely because the connection
    is already broken, so a close that also fails has changed nothing. What
    matters is that the pool sees a closed connection when the block exits.
    """
    try:
        await conn.close(timeout=DEAD_RELEASE_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 — it was dead before we got here
        logger.debug("closing a dead connection also failed: %s", exc)


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

    **A pooled connection is checked for life before it is handed over**
    (``DB_POOL_PING_ON_ACQUIRE``). A managed Postgres reaps idle connections on
    its own schedule, and asyncpg cannot tell: ``is_closed()`` stays False until
    a write fails, so the pool will happily lend out a socket the server closed
    minutes ago and the caller's query dies with ``ConnectionDoesNotExistError``.
    That reached a user here as a 500 from ``/auth/me`` and a page reading
    "Can't reach the API". A dead connection is now discarded and another taken.
    """
    if not isinstance(source, asyncpg.Pool):
        yield source
        return

    global _last_activity

    settings = get_settings()
    started = time.perf_counter()
    attempts = max(1, settings.DB_POOL_ACQUIRE_ATTEMPTS)
    # Probe only after a gap in traffic. See DB_POOL_PING_AFTER_IDLE_S — a probe
    # on every checkout measured at +256 ms against this database, which is a
    # price worth paying once after a quiet spell and not on every request.
    quiet_for = time.monotonic() - _last_activity
    probe = (
        settings.DB_POOL_PING_ON_ACQUIRE
        and quiet_for >= settings.DB_POOL_PING_AFTER_IDLE_S
    )

    # `async with source.acquire()` and not `await` + `release`: asyncpg's pool
    # supports both, but the second form is not what every caller's test double
    # implements, and changing a contract to add a probe would be paying for
    # this fix in someone else's code.
    for attempt in range(1, attempts + 1):
        async with source.acquire() as conn:
            if probe and not await _is_live(conn):
                # Dead on arrival: the server dropped it while it sat idle and
                # asyncpg had no way to know. Closing it is what makes the pool
                # discard rather than re-lend it when this block exits.
                logger.info(
                    "discarding a dead pooled connection (attempt %d/%d)",
                    attempt,
                    attempts,
                )
                metrics.db_pool_dead_connections.inc()
                await _discard(conn)
                if attempt < attempts:
                    continue
                # Out of attempts. Fall through and let the caller's own query
                # produce the real error rather than inventing one here.
            metrics.db_pool_acquire_wait.observe(time.perf_counter() - started)
            try:
                yield conn
            finally:
                # Set on the way out, not the way in: the block having run is
                # the evidence the connection worked. Recording it at checkout
                # would mark a pool healthy on the strength of a handout that
                # then failed.
                _last_activity = time.monotonic()
            return


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
