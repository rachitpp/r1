"""ARQ worker entrypoint (SPEC §10).

Runs the ingest pipeline off the HTTP path: the API enqueues ``ingest_repo`` and
returns immediately (CLAUDE.md hard rule 1), and this process does the clone,
parse, symbol pass, and embedding, writing progress to the repo row as it goes.

    uv run arq app.worker.WorkerSettings

Three operational choices are load-bearing and explained where they are set
below: the 2-second poll delay (Upstash command budget), the zombie sweep on
startup, and who writes ``repos.error``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID

from arq.connections import RedisSettings

from app.config import ZOMBIE_AFTER_S, get_settings
from app.db import queries
from app.db.pool import close_pool, create_pool
from app.ingest.embedder import get_embedder
from app.ingest.pipeline import run_ingest

logger = logging.getLogger("app.worker")

# SPEC §10. `job_timeout` must stay comfortably below ZOMBIE_AFTER_S (1200s) so
# a slow-but-live job is never swept out from under itself.
JOB_TIMEOUT_S = 900
MAX_TRIES = 2

# Redis polling cadence. ARQ's 0.5s default is ~172_800 commands/day per worker
# — a managed free tier (Upstash: 500K commands/month) is gone in three days
# with the worker merely idling. At 2s it is ~43_200/day, and the cost is up to
# two seconds of extra latency before a submitted repo starts cloning, against a
# job that takes minutes. Logged in DECISIONS as a Phase 6 deploy consideration.
POLL_DELAY_S = 2.0


async def ping(ctx: dict[str, Any]) -> str:
    """No-op health task (Phase 0; kept as a queue smoke test)."""
    return "pong"


async def ingest_repo(ctx: dict[str, Any], repo_id: str) -> str:
    """Ingest one repo (SPEC §10). Owns the ``failed`` status and error text.

    :func:`run_ingest` raises rather than recording failures itself, because the
    CLI and the queue want different things from a failure (a stderr line vs. a
    row a UI can render). This is the queue's answer: status ``failed`` plus the
    exception text on the row.

    ``CancelledError`` is re-raised untouched — that is ARQ timing the job out or
    aborting it, and it must stay a retryable outcome (``max_tries=2``, retry
    re-enters cleanly because the pipeline deletes and replaces at the start).
    Anything the retry does not fix is caught by the zombie sweep.
    """
    rid = UUID(str(repo_id))
    pool = ctx["pool"]
    try:
        stats = await run_ingest(
            rid, pool=pool, log=lambda m: logger.info("[%s] %s", repo_id, m)
        )
    except asyncio.CancelledError:
        logger.warning("ingest cancelled for %s (timeout or abort)", repo_id)
        raise
    except Exception as exc:  # noqa: BLE001 — every failure belongs on the row
        logger.exception("ingest failed for %s", repo_id)
        async with pool.acquire() as conn:
            await queries.fail_repo(conn, rid, f"{type(exc).__name__}: {exc}")
        return f"failed: {type(exc).__name__}: {exc}"

    summary = (
        f"ready: {stats.name} files={stats.selection.n_kept} "
        f"chunks={len(stats.chunks)} symbols={stats.n_symbols} edges={stats.n_edges}"
    )
    logger.info("[%s] %s", repo_id, summary)
    return summary


async def on_startup(ctx: dict[str, Any]) -> None:
    """Open the pool, warm the embedder, and sweep zombies (SPEC §4, §10)."""
    # arq's CLI configures the `arq` logger and leaves the root logger alone, so
    # without this the pipeline's per-state progress lines go nowhere — the one
    # place an operator looks when an ingest seems stuck.
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    settings = get_settings()
    ctx["pool"] = await create_pool(settings.DATABASE_URL)
    logger.info(
        "arq worker up | embedding_model=%s | agent_model=%s | poll_delay=%ss",
        settings.EMBEDDING_MODEL,
        settings.AGENT_MODEL or "<unset>",
        POLL_DELAY_S,
    )

    # Models load once per process, never per job (SPEC §4).
    get_embedder()

    # Any row still claiming to be mid-ingest is the corpse of a previous
    # worker: the job that owned it died with it, so nothing will ever move it
    # again. Startup is the moment we know for certain no such job is running.
    async with ctx["pool"].acquire() as conn:
        swept = await queries.sweep_zombie_repos(conn, ZOMBIE_AFTER_S)
    if swept:
        logger.warning("zombie sweep failed %d stale repo(s): %s", len(swept), swept)


async def on_shutdown(ctx: dict[str, Any]) -> None:
    await close_pool(ctx.get("pool"))


class WorkerSettings:
    """ARQ worker configuration. ``arq`` reads these class attributes."""

    functions = [ping, ingest_repo]
    on_startup = on_startup
    on_shutdown = on_shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().REDIS_URL)
    job_timeout = JOB_TIMEOUT_S
    max_tries = MAX_TRIES
    poll_delay = POLL_DELAY_S
    # One repo at a time: ingestion is CPU-bound (tree-sitter, Jedi, embedding)
    # on a 4-core box, so concurrent jobs would only make both slower.
    max_jobs = 1
