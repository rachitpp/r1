"""Regression: `_` is a SQL LIKE wildcard, and Python identifiers are full of it.

Found on 2026-08-02 by comparing `LIKE '%.' || name` against a real suffix test
across every indexed corpus. flask produced two false matches, and both were
*materially* wrong rather than merely extra — `find_symbol` orders by shortest
qualname, so the bogus short match beat the real symbol every time:

    json_dumps       -> src.flask.json.dumps            (real: EnvironBuilder.json_dumps)
    test_cli_runner  -> tests.test_cli.runner           (real: Flask.test_cli_runner)

`get_definition`, `find_references` and `expand_context` all resolved symbols
this way, so the agent could read, quote and cite the wrong function.

Needs a real database: the defect is in SQL pattern semantics, and a fake
connection would be asserting against my own reimplementation of LIKE.
"""

from __future__ import annotations

import asyncio
import uuid

import asyncpg
import pytest

from app.config import get_settings
from app.db import queries

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


async def _seed(conn: asyncpg.Connection) -> uuid.UUID:
    """A source + snapshot holding the exact shape that broke, and nothing else."""
    url = f"https://example.invalid/wildcard-{uuid.uuid4()}"
    source_id = await queries.get_or_create_source(conn, url=url, name="test/wildcard")
    snapshot_id = await queries.create_snapshot(conn, source_id)
    await queries.insert_symbols(
        conn,
        snapshot_id,
        [
            # The decoy: shorter qualname, so it wins any length-ordered tie.
            ("dumps", "pkg.json.dumps", "function", "pkg/json.py", 1, 5, False),
            # The real thing the caller asked for.
            (
                "json_dumps",
                "pkg.testing.Builder.json_dumps",
                "method",
                "pkg/testing.py",
                10,
                20,
                False,
            ),
        ],
    )
    return snapshot_id


async def test_an_underscore_does_not_match_a_dot(require_db: None) -> None:
    conn = await asyncpg.connect(get_settings().DATABASE_URL)
    try:
        snapshot_id = await _seed(conn)
        try:
            found = await queries.find_symbol(conn, snapshot_id, "json_dumps")
            assert found is not None, "the real symbol must still resolve"
            assert found["qualname"] == "pkg.testing.Builder.json_dumps"
            # The precise failure: `pkg.json.dumps` is shorter, so under LIKE it
            # was returned instead — a different function, in a different file.
            assert found["qualname"] != "pkg.json.dumps"
        finally:
            await conn.execute(
                "DELETE FROM repo_sources WHERE name = 'test/wildcard'"
            )
    finally:
        await conn.close()


async def test_an_exact_suffix_still_resolves(require_db: None) -> None:
    """The fix must not break the thing LIKE was there for."""
    conn = await asyncpg.connect(get_settings().DATABASE_URL)
    try:
        snapshot_id = await _seed(conn)
        try:
            found = await queries.find_symbol(conn, snapshot_id, "dumps")
            assert found is not None
            assert found["qualname"] == "pkg.json.dumps"
        finally:
            await conn.execute(
                "DELETE FROM repo_sources WHERE name = 'test/wildcard'"
            )
    finally:
        await conn.close()


async def test_a_percent_in_a_query_is_not_a_wildcard_either(
    require_db: None,
) -> None:
    """`%` is the other LIKE metacharacter, and a name is user/model input."""
    conn = await asyncpg.connect(get_settings().DATABASE_URL)
    try:
        snapshot_id = await _seed(conn)
        try:
            assert await queries.find_symbol(conn, snapshot_id, "%") is None
            assert await queries.find_symbol(conn, snapshot_id, "%dumps") is None
        finally:
            await conn.execute(
                "DELETE FROM repo_sources WHERE name = 'test/wildcard'"
            )
    finally:
        await conn.close()
