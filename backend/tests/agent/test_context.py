"""§7.4 context assembly: called-by blocks and the oversize part-n fallback.

The fallback is the point of these tests. `backfill_chunk_symbol_ids` links
only `part = 1`, so a long function's later parts carry `symbol_id IS NULL`
by design — and a consumer that trusts that column silently drops caller
annotations on every part but the first.
"""

from __future__ import annotations

import uuid

import pytest

from app.agent.context import called_by_block, format_called_by
from app.ingest.chunker import chunk_file
from app.ingest.tokens import HeuristicTokenCounter
from tests.ingest.conftest import parse_source

REPO_ID = uuid.uuid4()


# --- formatting ------------------------------------------------------------


def test_format_called_by_renders_sites() -> None:
    block = format_called_by(
        [("api/routes.py", 34, "handle_login"), ("api/routes.py", 78, "refresh")], 2
    )
    assert block == (
        "# Called by: api/routes.py:34 (handle_login), api/routes.py:78 (refresh)"
    )


def test_format_called_by_empty_when_no_callers() -> None:
    assert format_called_by([], 0) == ""


def test_format_called_by_caps_and_reports_remainder() -> None:
    callers = [(f"m{i}.py", i, f"f{i}") for i in range(20)]
    block = format_called_by(callers, 20, limit=8)
    assert block.count("(") == 8
    assert block.endswith("+12 more")


# --- part-n fallback -------------------------------------------------------


class _FakeConn:
    """Minimal asyncpg-shaped stub: records queries, returns canned rows."""

    def __init__(self, symbol_rows: list[dict], caller_rows: list[dict]) -> None:
        self.symbol_rows = symbol_rows
        self.caller_rows = caller_rows
        self.fetchrow_calls = 0

    async def fetchrow(self, _sql: str, *_args: object) -> dict | None:
        self.fetchrow_calls += 1
        return self.symbol_rows[0] if self.symbol_rows else None

    async def fetch(self, _sql: str, *_args: object) -> list[dict]:
        return self.caller_rows

    async def fetchval(self, _sql: str, *_args: object) -> int:
        return len(self.caller_rows)


CALLERS = [{"file_path": "pkg/caller.py", "line": 12, "name": "invoke"}]


@pytest.mark.asyncio
async def test_part_one_uses_symbol_id_directly() -> None:
    conn = _FakeConn([], CALLERS)
    block = await called_by_block(
        conn,  # type: ignore[arg-type]
        REPO_ID,
        symbol_id=42,
        file_path="pkg/big.py",
        qualname="pkg.big.huge",
    )
    assert "pkg/caller.py:12 (invoke)" in block
    assert conn.fetchrow_calls == 0, "no fallback lookup needed when symbol_id is set"


@pytest.mark.asyncio
async def test_part_two_falls_back_to_qualname() -> None:
    """The regression this guards: part 2 has NULL symbol_id but real callers."""
    conn = _FakeConn([{"id": 42}], CALLERS)
    block = await called_by_block(
        conn,  # type: ignore[arg-type]
        REPO_ID,
        symbol_id=None,  # what an oversize part 2 actually carries
        file_path="pkg/big.py",
        qualname="pkg.big.huge",
    )
    assert conn.fetchrow_calls == 1, "expected the qualname fallback to run"
    assert "pkg/caller.py:12 (invoke)" in block


@pytest.mark.asyncio
async def test_unresolvable_chunk_yields_empty_block() -> None:
    conn = _FakeConn([], CALLERS)
    block = await called_by_block(
        conn,  # type: ignore[arg-type]
        REPO_ID,
        symbol_id=None,
        file_path="pkg/big.py",
        qualname=None,
    )
    assert block == ""


# --- the oversize shape this exists for ------------------------------------


def _oversized_function(n_statements: int = 220) -> str:
    """A function whose body exceeds CHUNK_TOKEN_MAX under the heuristic counter."""
    body = "\n".join(f"    value_{i} = compute_something({i})" for i in range(n_statements))
    return f"def huge(seed):\n{body}\n    return seed\n"


def test_oversize_function_splits_with_only_part_one_carrying_start_line() -> None:
    """Establishes the precondition: parts 2..n start at shifted lines.

    That shift is exactly why `backfill_chunk_symbol_ids` cannot link them, and
    why `called_by_block` must fall back on qualname instead.
    """
    parsed = parse_source("pkg/big.py", _oversized_function())
    chunks = [c for c in chunk_file(parsed, HeuristicTokenCounter()) if c.kind == "function"]
    assert len(chunks) > 1, "fixture must actually exceed the oversize threshold"
    assert chunks[0].part == 1
    later = chunks[1]
    assert later.part == 2
    assert later.start_line != chunks[0].start_line
    # All parts share the qualname — the key the fallback keys on.
    assert {c.symbol for c in chunks} == {"pkg.big.huge"}
