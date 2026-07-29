"""Integration tests: real DB + real embedder.

Marked ``integration`` and skipped cleanly when the configured database is
unreachable. Builds a throwaway git repo, ingests it twice to prove
delete-and-replace idempotency, and smoke-tests hybrid retrieval against it.
Everything written is deleted in teardown.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import asyncpg
import pytest

from app.config import get_settings
from app.db import queries
from app.db.pool import close_pool, create_pool
from app.db.queries import count_chunks, resolve_snapshot_id
from app.ingest.cli import ingest_to_db
from app.ingest.pipeline import run_ingest
from app.retrieval.hybrid import search

pytestmark = pytest.mark.integration


async def _db_reachable() -> bool:
    try:
        conn = await asyncpg.connect(get_settings().DATABASE_URL, timeout=5)
    except Exception:
        return False
    await conn.close()
    return True


@pytest.fixture
def require_db() -> None:
    if not asyncio.run(_db_reachable()):
        pytest.skip("database unreachable")


def _make_git_repo(root: Path) -> Path:
    repo = root / "mini"
    repo.mkdir()
    (repo / "a.py").write_text(
        "import os\n\n\ndef alpha_helper(path):\n"
        '    """Join a path segment."""\n'
        "    return os.path.join(path, 'a')\n"
    )
    (repo / "b.py").write_text(
        'class BetaWidget:\n    """A small widget."""\n\n'
        "    def render(self):\n        return 'beta'\n"
    )
    (repo / "c.py").write_text(
        "GAMMA_CONST = 42\n\n\ndef gamma_func():\n    return GAMMA_CONST\n"
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


def _add_commit(repo: Path, filename: str, body: str) -> None:
    """A second commit, so the next ingest resolves to a different SHA.

    Without this the two ingests are of the *same* commit and §14.4 dedup
    correctly refuses to build a second corpus — which is the right behaviour
    and the wrong setup for testing isolation between two corpora.
    """

    def run(*args: str) -> None:
        subprocess.run(args, cwd=repo, check=True, capture_output=True)

    (repo / filename).write_text(body, encoding="utf-8")
    run("git", "add", ".")
    run(
        "git", "-c", "user.email=t@example.com", "-c", "user.name=tester",
        "commit", "-q", "-m", "second",
    )


async def _delete_repo(url: str) -> None:
    pool = await create_pool(get_settings().DATABASE_URL)
    try:
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM repos WHERE url = $1", url)
    finally:
        await close_pool(pool)


async def test_db_ingest_idempotent_and_search(
    tmp_path: Path, require_db: None
) -> None:
    repo = _make_git_repo(tmp_path)
    url = repo.as_uri()
    try:
        first = await ingest_to_db(url)
        assert first.selection.n_kept == 3
        assert len(first.chunks) >= 3

        # Re-ingest: delete-and-replace must leave counts identical (not doubled).
        second = await ingest_to_db(url)
        assert len(second.chunks) == len(first.chunks)

        pool = await create_pool(get_settings().DATABASE_URL)
        try:
            async with pool.acquire() as conn:
                snapshot_id = await resolve_snapshot_id(conn, url)
                assert snapshot_id is not None

                stored = await count_chunks(conn, snapshot_id)
                assert stored == len(first.chunks)

                n_repo_rows = await conn.fetchval(
                    "SELECT count(*) FROM repos WHERE url = $1", url
                )
                assert n_repo_rows == 1  # upsert, not duplicate

                hits = await search(
                    conn, snapshot_id, "alpha helper join path", k=5, mode="hybrid+rerank"
                )
                assert hits
                assert any(h["file_path"] == "a.py" for h in hits)
        finally:
            await close_pool(pool)
    finally:
        await _delete_repo(url)


async def test_retrieval_order_survives_a_row_rewrite(
    tmp_path: Path, require_db: None
) -> None:
    """Tied results must not depend on physical row order.

    Every ORDER BY in `hybrid.py` carries `id` as a tiebreaker. Without one,
    tied rows come back in heap order, so an UPDATE — which writes each row
    version to a new location — silently reshuffles results. That is not
    hypothetical: it moved fts MRR 0.503 -> 0.494 on the benchmark corpus when
    a migration rewrote 104 chunk rows (DECISIONS 2026-07-29).

    A value-preserving UPDATE is the exact mechanism, so the assertion is that
    retrieval returns the identical id sequence across one.
    """
    repo = _make_git_repo(tmp_path)
    url = repo.as_uri()
    try:
        await ingest_to_db(url)
        pool = await create_pool(get_settings().DATABASE_URL, command_timeout=None)
        try:
            async with pool.acquire() as conn:
                snapshot_id = await resolve_snapshot_id(conn, url)
                assert snapshot_id is not None

                query = "alpha helper join path"
                before = [
                    [h["chunk_id"] for h in await search(conn, snapshot_id, query, mode=m)]
                    for m in ("vector", "fts", "hybrid")
                ]
                assert any(before), "fixture produced no hits to compare"

                rewritten = await conn.execute(
                    "UPDATE chunks SET part = part WHERE snapshot_id = $1", snapshot_id
                )
                assert rewritten != "UPDATE 0"

                after = [
                    [h["chunk_id"] for h in await search(conn, snapshot_id, query, mode=m)]
                    for m in ("vector", "fts", "hybrid")
                ]
                assert after == before
        finally:
            await close_pool(pool)
    finally:
        await _delete_repo(url)


async def test_a_ready_snapshot_survives_another_ingest_of_the_same_source(
    tmp_path: Path, require_db: None
) -> None:
    """The race SPEC §14 exists to remove, against a real database.

    Before the split there was one row per URL and every ingest began by
    clearing its content, so a second ingest of the same repo deleted the corpus
    the first was serving — mid-chat, silently. Now a second ingest is a
    *different snapshot*: same source, its own rows.

    Asserted the way it would actually fail: capture the exact chunk ids the
    first snapshot serves, run a full second ingest of the same source, then
    search the first snapshot again and require the identical id sequence.
    """
    repo = _make_git_repo(tmp_path)
    url = repo.as_uri()
    try:
        await ingest_to_db(url)
        pool = await create_pool(get_settings().DATABASE_URL, command_timeout=None)
        try:
            async with pool.acquire() as conn:
                first = await resolve_snapshot_id(conn, url)
                assert first is not None
                query = "alpha helper join path"
                before = [
                    h["chunk_id"]
                    for h in await search(conn, first, query, mode="hybrid")
                ]
                assert before, "fixture produced no hits to compare"
                n_before = await count_chunks(conn, first)

                source = await queries.source_of(conn, first)
                assert source is not None

            # A genuinely different commit, or §14.4 dedup would (rightly)
            # decline to build a second corpus at all.
            _add_commit(repo, "c.py", "def gamma():\n    return 3\n")

            async with pool.acquire() as conn:
                second = await queries.create_snapshot(conn, source["source_id"])
                assert second != first

            await run_ingest(second, pool=pool)

            async with pool.acquire() as conn:
                after = [
                    h["chunk_id"]
                    for h in await search(conn, first, query, mode="hybrid")
                ]
                # The original corpus is byte-for-byte the one it was.
                assert after == before
                assert await count_chunks(conn, first) == n_before
                # And the new snapshot is a genuinely separate corpus.
                assert await count_chunks(conn, second) > 0
        finally:
            await close_pool(pool)
    finally:
        await _delete_repo(url)
