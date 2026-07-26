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
