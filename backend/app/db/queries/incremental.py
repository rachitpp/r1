"""Incremental re-indexing (SPEC §29, FEATURE-IDEAS 5.3)."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

import asyncpg

# ---------------------------------------------------------------------------
# Incremental re-indexing (SPEC §29, FEATURE-IDEAS 5.3)
# ---------------------------------------------------------------------------


async def set_snapshot_embedding_model(
    conn: asyncpg.Connection, snapshot_id: UUID, model: str
) -> None:
    """Record which model produced this snapshot's vectors (§29.1)."""
    await conn.execute(
        "UPDATE repo_snapshots SET embedding_model = $2 WHERE id = $1",
        snapshot_id,
        model,
    )


async def reusable_snapshot(
    conn: asyncpg.Connection,
    source_id: UUID,
    *,
    exclude: UUID,
    strategy: str,
    embedding_model: str,
) -> UUID | None:
    """The newest snapshot whose chunks this ingest may copy from (§29.2).

    Four conditions, and every one of them is a correctness requirement rather
    than an optimisation:

    * **same source** — obviously.
    * **same strategy** — `naive` and `ast` cut different chunks from identical
      text, so a chunk copied across them describes lines it does not cover.
    * **same embedding model** — the vectors have to live in the same space.
      `NULL` (anything ingested before migration 016) is excluded by the
      equality, which is the intent: unknown is not a match.
    * **ready** — a half-written corpus is not a source of truth for anything.
    """
    row = await conn.fetchrow(
        """
        SELECT id FROM repo_snapshots
         WHERE source_id = $1
           AND id <> $2
           AND strategy = $3
           AND embedding_model = $4
           AND status = 'ready'
         ORDER BY created_at DESC
         LIMIT 1
        """,
        source_id,
        exclude,
        strategy,
        embedding_model,
    )
    return UUID(str(row["id"])) if row else None


async def file_digests(
    conn: asyncpg.Connection, snapshot_id: UUID
) -> dict[str, str]:
    """``path -> md5(content)`` for a snapshot (§29.2).

    Hashes rather than contents: the comparison needs equality, not the text,
    and shipping a whole corpus over the wire to decide what not to re-embed
    would spend most of what the feature saves.
    """
    rows = await conn.fetch(
        "SELECT path, md5(content) AS digest FROM files WHERE snapshot_id = $1",
        snapshot_id,
    )
    return {str(r["path"]): str(r["digest"]) for r in rows}


async def copy_chunks(
    conn: asyncpg.Connection,
    from_snapshot: UUID,
    to_snapshot: UUID,
    paths: Sequence[str],
) -> int:
    """Copy chunk rows — vectors included — for unchanged files (§29.3).

    The whole point of §29: an embedding is a pure function of the chunk text,
    and the chunk text is a pure function of the file content and the chunker.
    Both are known identical here, so re-deriving the vector would spend a
    forward pass to arrive at the bytes already sitting in the row.

    ``symbol_id`` is deliberately *not* copied. It points into the previous
    snapshot's `symbols` table, and the backfill after the symbol pass resolves
    it against this one — copying it would leave every reused chunk pointing at
    another corpus's symbol row.

    ``is_prose`` *is* copied, with ``is_test``. Both are properties of the chunk
    rather than of the snapshot, and re-deriving them here would duplicate the
    §30.4 mapping in a second place that could drift from the first.
    """
    if not paths:
        return 0
    result = await conn.fetch(
        """
        INSERT INTO chunks
          (snapshot_id, file_path, symbol, kind, part, n_parts,
           start_line, end_line, header, code, embedding, is_test, is_prose)
        SELECT $2, file_path, symbol, kind, part, n_parts,
               start_line, end_line, header, code, embedding, is_test, is_prose
          FROM chunks
         WHERE snapshot_id = $1
           AND file_path = ANY($3::text[])
        RETURNING id
        """,
        from_snapshot,
        to_snapshot,
        list(paths),
    )
    return len(result)
