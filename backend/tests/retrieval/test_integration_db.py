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
from app.db.pool import close_pool, create_pool
from app.db.queries import count_chunks, resolve_repo_id
from app.ingest.cli import ingest_to_db
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
                repo_id = await resolve_repo_id(conn, url)
                assert repo_id is not None

                stored = await count_chunks(conn, repo_id)
                assert stored == len(first.chunks)

                n_repo_rows = await conn.fetchval(
                    "SELECT count(*) FROM repos WHERE url = $1", url
                )
                assert n_repo_rows == 1  # upsert, not duplicate

                hits = await search(
                    conn, repo_id, "alpha helper join path", k=5, mode="hybrid+rerank"
                )
                assert hits
                assert any(h["file_path"] == "a.py" for h in hits)
        finally:
            await close_pool(pool)
    finally:
        await _delete_repo(url)
