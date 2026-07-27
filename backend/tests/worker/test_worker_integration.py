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

from app.config import ZOMBIE_AFTER_S, get_settings
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


async def test_queued_ingest_reaches_ready_and_is_idempotent(
    tmp_path: Path, require_services: None
) -> None:
    url = _make_git_repo(tmp_path).as_uri()
    pool = await create_pg_pool(get_settings().DATABASE_URL)
    try:
        async with pool.acquire() as conn:
            repo_id, created = await queries.create_repo(
                conn, url=url, name="local/tiny"
            )
            assert created

        await _run_queue_once(repo_id)

        async with pool.acquire() as conn:
            row = await queries.get_repo(conn, repo_id)
            assert row is not None
            assert row["status"] == "ready", row["error"]
            assert row["files_total"] == 2
            assert row["files_parsed"] == 2
            assert row["chunks_total"] > 0
            assert row["chunks_embedded"] == row["chunks_total"]
            assert row["head_sha"]
            first_chunks = await queries.count_chunks(conn, repo_id)
            n_symbols = await conn.fetchval(
                "SELECT count(*) FROM symbols WHERE repo_id = $1", repo_id
            )
            assert first_chunks > 0
            assert n_symbols > 0

        # Re-ingest through the queue: delete-and-replace, not accumulate (§10).
        await _run_queue_once(repo_id)

        async with pool.acquire() as conn:
            row = await queries.get_repo(conn, repo_id)
            assert row is not None
            assert row["status"] == "ready"
            assert await queries.count_chunks(conn, repo_id) == first_chunks
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM symbols WHERE repo_id = $1", repo_id
                )
                == n_symbols
            )
            assert (
                await conn.fetchval("SELECT count(*) FROM repos WHERE url = $1", url)
                == 1
            )
    finally:
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM repos WHERE url = $1", url)
        await close_pool(pool)


async def test_zombie_sweep_fails_stale_rows_only(
    require_services: None,
) -> None:
    """A row abandoned mid-embed is failed; a live one of the same status is not."""
    stale_url = "https://example.invalid/zombie-stale"
    fresh_url = "https://example.invalid/zombie-fresh"
    pool = await create_pg_pool(get_settings().DATABASE_URL)
    try:
        async with pool.acquire() as conn:
            stale_id, _ = await queries.create_repo(
                conn, url=stale_url, name="zombie/stale"
            )
            fresh_id, _ = await queries.create_repo(
                conn, url=fresh_url, name="zombie/fresh"
            )
            await conn.execute(
                """
                UPDATE repos
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
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM repos WHERE url = ANY($1::text[])",
                [stale_url, fresh_url],
            )
        await close_pool(pool)
