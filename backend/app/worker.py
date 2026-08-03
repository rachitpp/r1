"""ARQ worker entrypoint (SPEC §10).

Runs the ingest pipeline off the HTTP path: the API enqueues ``ingest_repo`` and
returns immediately (CLAUDE.md hard rule 1), and this process does the clone,
parse, symbol pass, and embedding, writing progress to the repo row as it goes.

    uv run python -m app.worker            # supervised: restarts its own loop
    uv run arq app.worker.WorkerSettings   # bare: right under systemd/Docker

Three operational choices are load-bearing and explained where they are set
below: the 2-second poll delay (Upstash command budget), the zombie sweep on
startup, and who writes ``repos.error``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import socket
from typing import Any
from uuid import UUID

from app.config import (
    HEARTBEAT_EVERY_S,
    LEASE_EXPIRY_S,
    ZOMBIE_AFTER_S,
    get_settings,
)
from app.config import (
    redis_settings as build_redis_settings,
)
from app.db import queries
from app.db.pool import close_pool, create_pool
from app.exceptions import SnapshotSuperseded
from app.ingest.embedder import get_embedder
from app.ingest.pipeline import run_ingest
from app.logging_setup import configure_logging
from app.redact import safe_error_text

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


# Worker identity for the lease (§15.2): enough to find the process, never used
# as a lock — a hostname can repeat, so the lease is the timestamp, not the name.
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"


async def _heartbeat(pool: Any, snapshot_id: UUID) -> None:
    """Refresh the lease on a timer until cancelled (§15.4).

    A *timer*, not a progress hook — that distinction is the whole fix. The old
    sweep inferred liveness from progress writes, so the silent stretch inside
    `linking` (one status write, then Jedi to completion) was indistinguishable
    from a dead worker, and the window had to be sized for the quietest phase.

    Failures are logged and swallowed: a missed beat is survivable
    (LEASE_EXPIRY_S covers several), and letting this task die would silently
    stop the heartbeat for the rest of a long ingest.
    """
    while True:
        try:
            await asyncio.sleep(HEARTBEAT_EVERY_S)
            async with pool.acquire() as conn:
                await queries.touch_heartbeat(conn, snapshot_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — a missed beat is not fatal
            logger.warning("heartbeat failed for %s: %s", snapshot_id, exc)


async def ping(ctx: dict[str, Any]) -> str:
    """No-op health task (Phase 0; kept as a queue smoke test)."""
    return "pong"


async def ingest_repo(ctx: dict[str, Any], snapshot_id: str) -> str:
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
    rid = UUID(str(snapshot_id))
    pool = ctx["pool"]

    # §15.4. Claim the lease and start beating before any work begins, so a
    # crash at any point after this leaves a row the sweep can reclaim in
    # LEASE_EXPIRY_S rather than one that looks busy until ZOMBIE_AFTER_S.
    async with pool.acquire() as conn:
        claimed = await queries.claim_snapshot(conn, rid, WORKER_ID)
    if not claimed:
        # Another worker got there first, or the row is no longer queued. Not an
        # error: the §15.3 partial index exists precisely so this resolves
        # quietly instead of two workers ingesting the same repo.
        logger.info("[%s] not claimable (already taken or not queued)", snapshot_id)
        return "skipped: not claimable"

    beat = asyncio.create_task(_heartbeat(pool, rid))
    try:
        stats = await run_ingest(
            rid, pool=pool, log=lambda m: logger.info("[%s] %s", snapshot_id, m)
        )
    except SnapshotSuperseded as dedup:
        # A success, not a failure (SPEC §14.4): the clone found this commit
        # already ingested, the submitters were moved to the existing snapshot,
        # and this row is gone. Nothing to mark `failed` — there is no row left
        # to mark, and marking the kept one would be a lie about a good corpus.
        logger.info("[%s] %s", snapshot_id, dedup)
        return f"deduped: {dedup.kept_id}"
    except asyncio.CancelledError:
        logger.warning("ingest cancelled for %s (timeout or abort)", snapshot_id)
        raise
    except Exception as exc:  # noqa: BLE001 — every failure belongs on the row
        logger.exception("ingest failed for %s", snapshot_id)
        # Redacted, because `repos.error` is not an operator-only field: it is
        # served straight to the browser by `RepoOut`. A clone failure that
        # embeds a credentialed URL, or an asyncpg error carrying the DSN, would
        # otherwise be published to whoever submitted the repo. The unredacted
        # exception is in the log line above, with the traceback.
        detail = safe_error_text(exc, include_type=True)
        async with pool.acquire() as conn:
            await queries.fail_repo(conn, rid, detail)
        return f"failed: {detail}"
    finally:
        # Every exit path, including the two returns above and the re-raised
        # CancelledError: a heartbeat outliving its job would keep a dead row
        # looking alive for as long as the process lasts.
        beat.cancel()

    summary = (
        f"ready: {stats.name} files={stats.selection.n_kept} "
        f"chunks={len(stats.chunks)} symbols={stats.n_symbols} edges={stats.n_edges}"
    )
    logger.info("[%s] %s", snapshot_id, summary)
    return summary


async def generate_overview(ctx: dict[str, Any], snapshot_id: str) -> str:
    """Write a snapshot's §19 overview. One model call; owns the row's status.

    On the queue rather than in the request that triggered it, for the same
    reason ingestion is (CLAUDE.md rule 1): a model call is seconds to tens of
    seconds, and an HTTP handler that blocks for it holds a connection the whole
    time. The endpoint claims the row and returns 202; this does the work.

    The claim already happened — `queries.claim_overview` is what decided to
    enqueue — so this job never races another. A failure is recorded on the row
    and the row is left `failed`, which the endpoint clears on a retry rather
    than blocking on forever (the `010` lesson, one table over).
    """
    rid = UUID(str(snapshot_id))
    pool = ctx["pool"]
    settings = get_settings()
    model_name = settings.AGENT_MODEL or "(unset)"
    try:
        # Imported here, not at module scope: this pulls the provider package
        # and, through it, transformers — and the worker should pay that only
        # when it actually has an overview to write.
        from app.agent.model import build_chat_model
        from app.agent.overview import run_overview_job

        model = build_chat_model()
        async with pool.acquire() as conn:
            await run_overview_job(model, conn, rid, model_name)
    except asyncio.CancelledError:
        logger.warning("overview cancelled for %s", snapshot_id)
        raise
    except Exception as exc:  # noqa: BLE001 — every failure belongs on the row
        logger.exception("overview failed for %s", snapshot_id)
        detail = safe_error_text(exc, include_type=True)
        async with pool.acquire() as conn:
            await queries.fail_overview(conn, rid, detail)
        return f"failed: {detail}"
    logger.info("[%s] overview written", snapshot_id)
    return "ready"


async def on_startup(ctx: dict[str, Any]) -> None:
    """Open the pool, warm the embedder, and sweep zombies (SPEC §4, §10)."""
    # arq's CLI configures the `arq` logger and leaves the root logger alone, so
    # without this the pipeline's per-state progress lines go nowhere — the one
    # place an operator looks when an ingest seems stuck.
    configure_logging()
    settings = get_settings()
    # One job at a time (`max_jobs = 1`), so a handful of connections is plenty
    # — and `command_timeout=None` because the API's 30-second ceiling is wrong
    # here: a batched insert of a few thousand embeddings legitimately runs
    # longer than any HTTP request should.
    ctx["pool"] = await create_pool(
        settings.DATABASE_URL, min_size=1, max_size=4, command_timeout=None
    )
    logger.info(
        "arq worker up | embedding_model=%s | agent_model=%s | poll_delay=%ss",
        settings.EMBEDDING_MODEL,
        settings.AGENT_MODEL or "<unset>",
        POLL_DELAY_S,
    )

    # Models load once per process, never per job (SPEC §4).
    get_embedder()

    # Two sweeps, because they cover different rows and neither can reap what
    # the other owns (§15.4).
    #
    # The old comment here claimed "startup is the moment we know for certain no
    # such job is running" — which was never what the query did, and is false
    # with a worker fleet. `sweep_zombie_repos` has always been time-based, so
    # startup is simply a convenient moment to run it, not a guarantee.
    #
    # Leases are the precise mechanism: a heartbeat is unconditional, so an
    # expired one means the worker is gone, and LEASE_EXPIRY_S (120s) can be a
    # tenth of ZOMBIE_AFTER_S (1200s). The zombie sweep stays for rows with no
    # heartbeat at all — snapshots that predate the lease columns.
    async with ctx["pool"].acquire() as conn:
        expired = await queries.sweep_expired_leases(conn, LEASE_EXPIRY_S)
        legacy = await queries.sweep_zombie_repos(conn, ZOMBIE_AFTER_S)
    if expired:
        logger.warning("reclaimed %d snapshot(s) on expired leases: %s", len(expired), expired)
    if legacy:
        logger.warning("zombie sweep failed %d leaseless row(s): %s", len(legacy), legacy)


async def on_shutdown(ctx: dict[str, Any]) -> None:
    await close_pool(ctx.get("pool"))


class WorkerSettings:
    """ARQ worker configuration. ``arq`` reads these class attributes."""

    functions = [ping, ingest_repo, generate_overview]
    on_startup = on_startup
    on_shutdown = on_shutdown
    redis_settings = build_redis_settings()
    job_timeout = JOB_TIMEOUT_S
    max_tries = MAX_TRIES
    poll_delay = POLL_DELAY_S
    # One repo at a time: ingestion is CPU-bound (tree-sitter, Jedi, embedding)
    # on a 4-core box, so concurrent jobs would only make both slower.
    max_jobs = 1


# ---------------------------------------------------------------------------
# Supervised entrypoint
# ---------------------------------------------------------------------------

# Backoff between restarts. Short at first because the common case is a blip
# that has already passed, capped because a database that has been gone for a
# minute will not be back sooner for being asked more often.
RESTART_BACKOFF_S = (1.0, 5.0, 15.0, 30.0, 60.0)


async def _run_supervised() -> None:
    """Run the worker, restarting it if its own loop dies (§10).

    **Why this exists.** `retry_on_error` and `conn_timeout` (see
    `config.redis_settings`) make a Redis blip survivable *inside a job* — the
    retry is on the connection, so `run_job` recovers. ARQ's own poll and
    health-check loop is not covered by any of that: an exception there leaves
    `Worker.run()` and the process exits.

    Observed on this deployment: the worker completed three jobs, sat idle for
    54 minutes, and then died on a `TimeoutError` raised while polling — the
    free Redis tier drops connections nothing is using. The symptom is the one
    RUNNING.md §6 calls the most common broken-looking setup: a submitted repo
    sits at 0% forever with no error, because the thing that would have written
    the error is the thing that is gone.

    A supervisor is the right shape rather than another timeout. No timeout
    makes an unreachable server reachable; the honest response to "the queue
    went away" is to keep asking until it comes back.
    """
    from arq.worker import Worker

    attempt = 0
    while True:
        worker = Worker(
            functions=list(WorkerSettings.functions),  # type: ignore[arg-type]
            on_startup=WorkerSettings.on_startup,
            on_shutdown=WorkerSettings.on_shutdown,
            redis_settings=build_redis_settings(),
            job_timeout=WorkerSettings.job_timeout,
            max_tries=WorkerSettings.max_tries,
            poll_delay=WorkerSettings.poll_delay,
            max_jobs=WorkerSettings.max_jobs,
            handle_signals=False,
        )
        try:
            await worker.async_run()
            return  # A clean stop is a stop, not something to restart.
        except asyncio.CancelledError:
            raise  # Ctrl-C / SIGTERM: the operator meant it.
        except Exception as exc:  # noqa: BLE001 — the whole point is to survive
            delay = RESTART_BACKOFF_S[min(attempt, len(RESTART_BACKOFF_S) - 1)]
            attempt += 1
            logger.warning(
                "worker loop died (%s: %s); restarting in %.0fs [attempt %d]",
                type(exc).__name__,
                exc,
                delay,
                attempt,
            )
        finally:
            with contextlib.suppress(Exception):
                # Best-effort: closing talks to Redis, which is very likely the
                # thing that just failed.
                await worker.close()
        await asyncio.sleep(delay)


def main() -> None:
    """`python -m app.worker` — the supervised worker.

    `arq app.worker.WorkerSettings` still works and is still the documented
    command for anything with its own supervisor (systemd, Docker restart
    policies, Kubernetes). This is for the laptop, where "it was running
    yesterday" is otherwise the whole failure report.
    """
    configure_logging()
    try:
        asyncio.run(_run_supervised())
    except KeyboardInterrupt:
        logger.info("worker stopped")


if __name__ == "__main__":
    main()
