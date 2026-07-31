"""End-to-end queue tests: real Redis, real Postgres, real embedder.

Marked ``integration`` and skipped when either service is unreachable, like the
Phase 2 retrieval integration test. What these cover that no unit test can: that
a job handed to ARQ actually walks the §10 state machine on a real row, that
running it twice does not double the corpus, and that the zombie sweep moves a
genuinely stale row.

The queue name is test-specific so a developer's running worker cannot steal the
job — and so this test cannot steal theirs.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from uuid import UUID

import asyncpg
import pytest
from arq.connections import RedisSettings, create_pool
from arq.worker import Worker

from app.config import LEASE_EXPIRY_S, ZOMBIE_AFTER_S, get_settings
from app.db import queries
from app.db.pool import close_pool
from app.db.pool import create_pool as create_pg_pool
from app.worker import ingest_repo, on_shutdown, on_startup

pytestmark = pytest.mark.integration

TEST_QUEUE = "arq:queue:phase4-tests"


async def _reachable() -> bool:
    settings = get_settings()
    try:
        conn = await asyncpg.connect(settings.DATABASE_URL, timeout=5)
        await conn.close()
        redis = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
        await redis.ping()
        await redis.aclose()
    except Exception:
        return False
    return True


@pytest.fixture
def require_services() -> None:
    if not asyncio.run(_reachable()):
        pytest.skip("postgres or redis unreachable")


def _make_git_repo(root: Path) -> Path:
    repo = root / "tiny"
    repo.mkdir()
    (repo / "core.py").write_text(
        "def helper(value):\n"
        '    """Double a value."""\n'
        "    return value * 2\n\n\n"
        "def entry(value):\n"
        "    return helper(value) + 1\n"
    )
    (repo / "other.py").write_text(
        "from core import entry\n\n\nclass Runner:\n"
        "    def run(self, v):\n        return entry(v)\n"
    )

    def run(*args: str) -> None:
        subprocess.run(args, cwd=repo, check=True, capture_output=True)

    run("git", "init", "-q")
    run("git", "add", ".")
    run(
        "git",
        "-c",
        "user.email=t@example.com",
        "-c",
        "user.name=tester",
        "commit",
        "-q",
        "-m",
        "init",
    )
    return repo


async def _run_queue_once(repo_id: UUID) -> None:
    """Enqueue an ingest and drain the queue with an inline burst worker."""
    settings = get_settings()
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
    redis = await create_pool(redis_settings, default_queue_name=TEST_QUEUE)
    try:
        job = await redis.enqueue_job("ingest_repo", str(repo_id))
        assert job is not None
    finally:
        await redis.aclose()

    worker = Worker(
        functions=[ingest_repo],
        on_startup=on_startup,
        on_shutdown=on_shutdown,
        redis_settings=redis_settings,
        queue_name=TEST_QUEUE,
        burst=True,
        poll_delay=0.1,
        max_tries=1,
        job_timeout=600,
        keep_result=1,
    )
    await worker.main()
    await worker.close()


async def _new_snapshot(pool: asyncpg.Pool, url: str, name: str) -> UUID:
    """A source and one queued snapshot of it (§14.2).

    What `queries.create_repo` used to do in one call. V2 split it in two because
    the two halves have different lifetimes: a source is created once per URL and
    never rewritten, while a snapshot is created per ingest attempt and frozen.
    """
    async with pool.acquire() as conn:
        source_id = await queries.get_or_create_source(conn, url=url, name=name)
        return await queries.create_snapshot(conn, source_id)


async def _delete_source(pool: asyncpg.Pool, *urls: str) -> None:
    """Teardown. Deleting the source cascades to snapshots and all their content.

    Deliberately `repo_sources`, not `repos`: since V2 an ingest writes no
    `repos` row at all, so the old teardown deleted nothing and every run of
    this suite leaked a source, a snapshot, and its whole corpus.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM repo_sources WHERE url = ANY($1::text[])", list(urls)
        )


async def test_queued_ingest_reaches_ready_and_is_idempotent(
    tmp_path: Path, require_services: None
) -> None:
    url = _make_git_repo(tmp_path).as_uri()
    pool = await create_pg_pool(get_settings().DATABASE_URL)
    try:
        snapshot_id = await _new_snapshot(pool, url, "local/tiny")

        await _run_queue_once(snapshot_id)

        async with pool.acquire() as conn:
            row = await queries.get_repo(conn, snapshot_id)
            assert row is not None
            assert row["status"] == "ready", row["error"]
            assert row["files_total"] == 2
            assert row["files_parsed"] == 2
            assert row["chunks_total"] > 0
            assert row["chunks_embedded"] == row["chunks_total"]
            assert row["head_sha"]
            first_chunks = await queries.count_chunks(conn, snapshot_id)
            n_symbols = await conn.fetchval(
                "SELECT count(*) FROM symbols WHERE snapshot_id = $1", snapshot_id
            )
            assert first_chunks > 0
            assert n_symbols > 0

        # A second job for a snapshot that is already `ready` never starts: the
        # lease guards on `status = 'queued'` (§15.4). Asserted against the claim
        # itself rather than by running a worker and observing that nothing
        # changed — "nothing changed" is also what a silently broken queue looks
        # like, and a third worker spin-up costs a Redis connection on a free
        # tier whose command budget is already a documented constraint.
        async with pool.acquire() as conn:
            assert await queries.claim_snapshot(conn, snapshot_id, "test:probe") is False
            row = await queries.get_repo(conn, snapshot_id)
            assert row is not None
            assert row["status"] == "ready"  # the refused claim changed nothing
            assert await queries.count_chunks(conn, snapshot_id) == first_chunks

        # A *genuine* retry — the row put back to `queued`, which is what the
        # worker's failure path and an operator both do. Now the job really runs
        # again, and §10's delete-and-replace must leave the corpus identical
        # rather than doubled. Re-ingesting in place is correct here and is not
        # the §14.4 race: this snapshot is rebuilding *itself*, so the dedup
        # check finds no *other* ready snapshot at this commit and stands aside.
        async with pool.acquire() as conn:
            await queries.set_repo_status(conn, snapshot_id, "queued")

        await _run_queue_once(snapshot_id)

        async with pool.acquire() as conn:
            row = await queries.get_repo(conn, snapshot_id)
            assert row is not None
            assert row["status"] == "ready", row["error"]
            assert await queries.count_chunks(conn, snapshot_id) == first_chunks
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM symbols WHERE snapshot_id = $1", snapshot_id
                )
                == n_symbols
            )
            # One source for the URL, and one snapshot under it — the retry
            # rebuilt a row rather than adding one.
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM repo_sources WHERE url = $1", url
                )
                == 1
            )
            assert (
                await conn.fetchval(
                    """SELECT count(*) FROM repo_snapshots sn
                         JOIN repo_sources s ON s.id = sn.source_id
                        WHERE s.url = $1""",
                    url,
                )
                == 1
            )
    finally:
        await _delete_source(pool, url)
        await close_pool(pool)


async def test_zombie_sweep_fails_stale_rows_only(
    require_services: None,
) -> None:
    """A row abandoned mid-embed is failed; a live one of the same status is not."""
    stale_url = "https://example.invalid/zombie-stale"
    fresh_url = "https://example.invalid/zombie-fresh"
    pool = await create_pg_pool(get_settings().DATABASE_URL)
    try:
        # Two *different* sources on purpose: `repo_snapshots_one_in_flight`
        # (009) permits only one in-flight snapshot per (source, strategy), so
        # two in-flight rows under one source cannot exist to be compared.
        stale_id = await _new_snapshot(pool, stale_url, "zombie/stale")
        fresh_id = await _new_snapshot(pool, fresh_url, "zombie/fresh")

        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE repo_snapshots
                   SET status = 'embedding',
                       updated_at = now() - make_interval(secs => $2::float)
                 WHERE id = $1
                """,
                stale_id,
                ZOMBIE_AFTER_S + 60,
            )
            await queries.set_repo_status(conn, fresh_id, "embedding")

            swept = await queries.sweep_zombie_repos(conn, ZOMBIE_AFTER_S)
            assert str(stale_id) in swept
            assert str(fresh_id) not in swept

            stale = await queries.get_repo(conn, stale_id)
            fresh = await queries.get_repo(conn, fresh_id)
            assert stale is not None and fresh is not None
            assert stale["status"] == "failed"
            assert stale["error"] == "worker died"
            assert fresh["status"] == "embedding"

    finally:
        await _delete_source(pool, stale_url, fresh_url)
        await close_pool(pool)


async def test_the_two_sweeps_do_not_reap_each_other_s_rows(
    require_services: None,
) -> None:
    """§15.4: the lease sweep ignores leaseless rows, and vice versa.

    Both sweeps run back to back on every worker startup, over the same statuses.
    What keeps them from colliding is one predicate each — `heartbeat_at IS NOT
    NULL` on the lease sweep, and a much longer timer on the zombie sweep. That
    is a load-bearing detail with no test, and getting it wrong means a worker
    failing a snapshot another worker is actively ingesting.
    """
    leaseless_url = "https://example.invalid/sweep-leaseless"
    leased_url = "https://example.invalid/sweep-leased"
    pool = await create_pg_pool(get_settings().DATABASE_URL)
    try:
        leaseless_id = await _new_snapshot(pool, leaseless_url, "sweep/leaseless")
        leased_id = await _new_snapshot(pool, leased_url, "sweep/leased")

        async with pool.acquire() as conn:
            # Old and leaseless: predates the lease columns. Only the zombie
            # sweep may touch it.
            await conn.execute(
                """
                UPDATE repo_snapshots
                   SET status = 'embedding',
                       heartbeat_at = NULL,
                       updated_at = now() - make_interval(secs => $2::float)
                 WHERE id = $1
                """,
                leaseless_id,
                ZOMBIE_AFTER_S + 60,
            )
            # Leased and long expired, but touched recently enough that the
            # zombie timer does not reach it. Only the lease sweep may take it.
            await conn.execute(
                """
                UPDATE repo_snapshots
                   SET status = 'embedding',
                       claimed_by = 'ghost:1',
                       heartbeat_at = now() - make_interval(secs => $2::float),
                       updated_at = now()
                 WHERE id = $1
                """,
                leased_id,
                LEASE_EXPIRY_S + 60,
            )

            by_lease = await queries.sweep_expired_leases(conn, LEASE_EXPIRY_S)
            assert str(leased_id) in by_lease
            assert str(leaseless_id) not in by_lease

            by_timer = await queries.sweep_zombie_repos(conn, ZOMBIE_AFTER_S)
            assert str(leaseless_id) in by_timer
            # Already failed by the lease sweep, so it is no longer in-flight and
            # the second sweep cannot claim it a second time.
            assert str(leased_id) not in by_timer

            leased = await queries.get_repo(conn, leased_id)
            leaseless = await queries.get_repo(conn, leaseless_id)
            assert leased is not None and leaseless is not None
            assert leased["error"] == "worker lease expired"
            assert leaseless["error"] == "worker died"
    finally:
        await _delete_source(pool, leaseless_url, leased_url)
        await close_pool(pool)
