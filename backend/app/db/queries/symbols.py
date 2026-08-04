"""The symbol graph: definitions and edges (SPEC §3, §6)."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

import asyncpg

# --- Phase 3: symbol graph (SPEC §3, §6) -----------------------------------

SymbolRowT = tuple[
    str,  # name
    str,  # qualname
    str,  # kind
    str,  # file_path
    int,  # start_line
    int,  # end_line
    bool,  # is_test
]


async def insert_symbols(
    conn: asyncpg.Connection, snapshot_id: UUID, rows: Sequence[SymbolRowT]
) -> int:
    """Batch-insert symbol rows; return the count inserted."""
    if not rows:
        return 0
    await conn.executemany(
        """
        INSERT INTO symbols
          (snapshot_id, name, qualname, kind, file_path, start_line, end_line, is_test)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        """,
        [(snapshot_id, *row) for row in rows],
    )
    return len(rows)


async def symbol_id_map(
    conn: asyncpg.Connection, snapshot_id: UUID
) -> dict[tuple[str, int], int]:
    """Map ``(file_path, start_line)`` -> ``symbols.id`` for one repo.

    Extraction speaks in that natural key because ids are database-assigned;
    this is how edge rows get resolved to foreign keys after the symbol insert.
    """
    rows = await conn.fetch(
        "SELECT id, file_path, start_line FROM symbols WHERE snapshot_id = $1", snapshot_id
    )
    return {(str(r["file_path"]), int(r["start_line"])): int(r["id"]) for r in rows}


EdgeRowT = tuple[int, int, str, int | None]  # from_symbol, to_symbol, kind, line


async def insert_edges(
    conn: asyncpg.Connection, snapshot_id: UUID, rows: Sequence[EdgeRowT]
) -> int:
    """Batch-insert edge rows, ignoring duplicates; return rows offered.

    ``ON CONFLICT DO NOTHING`` covers the §3 unique key — the same call site
    can resolve to the same target twice across re-runs without erroring.
    """
    if not rows:
        return 0
    await conn.executemany(
        """
        INSERT INTO edges (snapshot_id, from_symbol, to_symbol, kind, line)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT DO NOTHING
        """,
        [(snapshot_id, *row) for row in rows],
    )
    return len(rows)


async def backfill_chunk_symbol_ids(conn: asyncpg.Connection, snapshot_id: UUID) -> int:
    """Link chunks to their defining symbol; return the number linked.

    Joins on ``(file_path, start_line)`` and is **restricted to ``part = 1``**.

    An oversize chunk's later parts carry a shifted ``start_line`` (SPEC §2.5)
    that can land exactly on a *different* symbol's start line — measured on
    httpx: 6 parts mislinked to the `__init__` they happened to begin at.
    Parts 2..n therefore stay NULL rather than pointing at the wrong symbol; a
    wrong link is worse than a missing one, and part 1 already carries the
    definition's identity.
    """
    result = await conn.execute(
        """
        UPDATE chunks c
           SET symbol_id = s.id
          FROM symbols s
         WHERE c.snapshot_id = $1
           AND s.snapshot_id = $1
           AND c.part = 1
           AND c.file_path = s.file_path
           AND c.start_line = s.start_line
        """,
        snapshot_id,
    )
    return int(result.split()[-1]) if result else 0


async def clear_repo_graph(conn: asyncpg.Connection, snapshot_id: UUID) -> None:
    """Delete symbols (and, by cascade, edges) for ``snapshot_id``.

    Chunk ``symbol_id`` values are ``ON DELETE`` unconstrained, so null them
    first to avoid dangling references after a delete-and-replace re-ingest.
    """
    await conn.execute(
        "UPDATE chunks SET symbol_id = NULL WHERE snapshot_id = $1", snapshot_id
    )
    await conn.execute("DELETE FROM symbols WHERE snapshot_id = $1", snapshot_id)


async def resolve_symbol_id(
    conn: asyncpg.Connection,
    snapshot_id: UUID,
    *,
    symbol_id: int | None,
    file_path: str,
    qualname: str | None,
) -> int | None:
    """Symbol id for a chunk, falling back for oversize parts 2..n.

    ``backfill_chunk_symbol_ids`` only links ``part = 1``, so a multi-part
    function's later parts carry ``symbol_id IS NULL`` by design. Every
    consumer that joins through ``symbol_id`` — §7.4 called-by assembly in
    particular — must come through here, or a long function silently loses its
    caller annotations on every part but the first.

    The fallback keys on ``(snapshot_id, file_path, qualname)``: parts share both
    with their definition, and the pair is unique per definition in practice.
    """
    if symbol_id is not None:
        return symbol_id
    if not qualname:
        return None
    row = await conn.fetchrow(
        """
        SELECT id FROM symbols
         WHERE snapshot_id = $1 AND file_path = $2 AND qualname = $3
         ORDER BY start_line
         LIMIT 1
        """,
        snapshot_id,
        file_path,
        qualname,
    )
    return None if row is None else int(row["id"])


async def implementation_callers(
    conn: asyncpg.Connection, snapshot_id: UUID, symbol_id: int, limit: int
) -> tuple[list[tuple[str, int, str]], int]:
    """Incoming call/import edges from **implementation** symbols (SPEC §7.4).

    Returns ``(rows, total)`` where each row is ``(file_path, line, name)`` and
    ``total`` is the unclipped count, so the caller can render "+N more".
    Test-side callers are excluded (§6.3): on a well-tested repo they outnumber
    the real call sites and would bury the answer.
    """
    rows = await conn.fetch(
        """
        SELECT s.file_path, COALESCE(e.line, s.start_line) AS line, s.name
          FROM edges e
          JOIN symbols s ON s.id = e.from_symbol
         WHERE e.snapshot_id = $1
           AND e.to_symbol = $2
           AND NOT s.is_test
         ORDER BY s.file_path, line
         LIMIT $3
        """,
        snapshot_id,
        symbol_id,
        limit,
    )
    total = await conn.fetchval(
        """
        SELECT count(*)
          FROM edges e JOIN symbols s ON s.id = e.from_symbol
         WHERE e.snapshot_id = $1 AND e.to_symbol = $2 AND NOT s.is_test
        """,
        snapshot_id,
        symbol_id,
    )
    return [(str(r["file_path"]), int(r["line"]), str(r["name"])) for r in rows], int(
        total
    )
