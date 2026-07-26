"""Write-path and lookup query helpers for the ingest CLI (SPEC §3, §10).

Batched inserts (``executemany``) rather than row-at-a-time. These functions
own no policy: they take a connection and typed args and run one statement
each. The retrieval fusion query lives in ``app/retrieval/hybrid.py`` (CLAUDE.md
hard rule 2), not here.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

import asyncpg

# Column order for chunk inserts — the embedding is last. ``id`` (identity),
# ``tsv`` (generated), ``part``/``n_parts`` defaults are handled by the table.
ChunkRow = tuple[
    str,  # file_path
    bool,  # is_test (derived from file_path, SPEC §2.6)
    str | None,  # symbol
    str,  # kind
    int,  # part
    int,  # n_parts
    int,  # start_line
    int,  # end_line
    str,  # header
    str,  # code
    list[float],  # embedding
]
FileRow = tuple[str, str, int]  # path, content, n_lines


async def upsert_repo(
    conn: asyncpg.Connection,
    *,
    url: str,
    name: str,
    head_sha: str,
    default_branch: str,
) -> UUID:
    """Insert or update the repo row for ``url``; return its id.

    Re-ingesting a known URL keeps the same id (so ``ON DELETE CASCADE`` and
    delete-and-replace target the same rows) and refreshes head/branch.
    """
    row = await conn.fetchrow(
        """
        INSERT INTO repos (url, name, head_sha, default_branch, status)
        VALUES ($1, $2, $3, $4, 'parsing')
        ON CONFLICT (url) DO UPDATE
          SET name = EXCLUDED.name,
              head_sha = EXCLUDED.head_sha,
              default_branch = EXCLUDED.default_branch,
              status = 'parsing',
              error = NULL,
              updated_at = now()
        RETURNING id
        """,
        url,
        name,
        head_sha,
        default_branch,
    )
    assert row is not None  # INSERT ... RETURNING always yields a row
    repo_id: UUID = row["id"]  # asyncpg decodes UUID columns to uuid.UUID
    return repo_id


async def clear_repo_content(conn: asyncpg.Connection, repo_id: UUID) -> None:
    """Delete all files and chunks for ``repo_id`` (delete-and-replace, §10)."""
    await conn.execute("DELETE FROM chunks WHERE repo_id = $1", repo_id)
    await conn.execute("DELETE FROM files WHERE repo_id = $1", repo_id)


async def insert_files(
    conn: asyncpg.Connection, repo_id: UUID, files: Sequence[FileRow]
) -> int:
    """Batch-insert file rows; return the count inserted."""
    if not files:
        return 0
    await conn.executemany(
        """
        INSERT INTO files (repo_id, path, content, n_lines)
        VALUES ($1, $2, $3, $4)
        """,
        [(repo_id, path, content, n_lines) for path, content, n_lines in files],
    )
    return len(files)


async def insert_chunks(
    conn: asyncpg.Connection, repo_id: UUID, rows: Sequence[ChunkRow]
) -> int:
    """Batch-insert chunk rows (embedding last); return the count inserted."""
    if not rows:
        return 0
    await conn.executemany(
        """
        INSERT INTO chunks
          (repo_id, file_path, is_test, symbol, kind, part, n_parts,
           start_line, end_line, header, code, embedding)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
        """,
        [(repo_id, *row) for row in rows],
    )
    return len(rows)


async def finalize_repo(
    conn: asyncpg.Connection,
    repo_id: UUID,
    *,
    files_total: int,
    files_parsed: int,
    chunks_total: int,
    status: str,
) -> None:
    """Write final counters and status for a completed ingest (SPEC §3, §10).

    Every stored chunk is embedded in this synchronous path, so
    ``chunks_embedded`` equals ``chunks_total``.
    """
    await conn.execute(
        """
        UPDATE repos
           SET files_total = $2, files_parsed = $3,
               chunks_total = $4, chunks_embedded = $4,
               status = $5, updated_at = now()
         WHERE id = $1
        """,
        repo_id,
        files_total,
        files_parsed,
        chunks_total,
        status,
    )


async def resolve_repo_id(conn: asyncpg.Connection, ref: str) -> UUID | None:
    """Resolve a repo by exact URL or by id string; return its id or ``None``."""
    row = await conn.fetchrow(
        "SELECT id FROM repos WHERE url = $1 OR id::text = $1", ref
    )
    if row is None:
        return None
    repo_id: UUID = row["id"]
    return repo_id


async def count_chunks(conn: asyncpg.Connection, repo_id: UUID) -> int:
    """Return the number of chunk rows stored for ``repo_id``."""
    value = await conn.fetchval("SELECT count(*) FROM chunks WHERE repo_id = $1", repo_id)
    return int(value)


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
    conn: asyncpg.Connection, repo_id: UUID, rows: Sequence[SymbolRowT]
) -> int:
    """Batch-insert symbol rows; return the count inserted."""
    if not rows:
        return 0
    await conn.executemany(
        """
        INSERT INTO symbols
          (repo_id, name, qualname, kind, file_path, start_line, end_line, is_test)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        """,
        [(repo_id, *row) for row in rows],
    )
    return len(rows)


async def symbol_id_map(
    conn: asyncpg.Connection, repo_id: UUID
) -> dict[tuple[str, int], int]:
    """Map ``(file_path, start_line)`` -> ``symbols.id`` for one repo.

    Extraction speaks in that natural key because ids are database-assigned;
    this is how edge rows get resolved to foreign keys after the symbol insert.
    """
    rows = await conn.fetch(
        "SELECT id, file_path, start_line FROM symbols WHERE repo_id = $1", repo_id
    )
    return {(str(r["file_path"]), int(r["start_line"])): int(r["id"]) for r in rows}


EdgeRowT = tuple[int, int, str, int | None]  # from_symbol, to_symbol, kind, line


async def insert_edges(
    conn: asyncpg.Connection, repo_id: UUID, rows: Sequence[EdgeRowT]
) -> int:
    """Batch-insert edge rows, ignoring duplicates; return rows offered.

    ``ON CONFLICT DO NOTHING`` covers the §3 unique key — the same call site
    can resolve to the same target twice across re-runs without erroring.
    """
    if not rows:
        return 0
    await conn.executemany(
        """
        INSERT INTO edges (repo_id, from_symbol, to_symbol, kind, line)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT DO NOTHING
        """,
        [(repo_id, *row) for row in rows],
    )
    return len(rows)


async def backfill_chunk_symbol_ids(conn: asyncpg.Connection, repo_id: UUID) -> int:
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
         WHERE c.repo_id = $1
           AND s.repo_id = $1
           AND c.part = 1
           AND c.file_path = s.file_path
           AND c.start_line = s.start_line
        """,
        repo_id,
    )
    return int(result.split()[-1]) if result else 0


async def clear_repo_graph(conn: asyncpg.Connection, repo_id: UUID) -> None:
    """Delete symbols (and, by cascade, edges) for ``repo_id``.

    Chunk ``symbol_id`` values are ``ON DELETE`` unconstrained, so null them
    first to avoid dangling references after a delete-and-replace re-ingest.
    """
    await conn.execute(
        "UPDATE chunks SET symbol_id = NULL WHERE repo_id = $1", repo_id
    )
    await conn.execute("DELETE FROM symbols WHERE repo_id = $1", repo_id)
