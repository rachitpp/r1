"""Generated repo overview storage (SPEC §19)."""

from __future__ import annotations

from uuid import UUID

import asyncpg


async def readme_sections(
    conn: asyncpg.Connection, snapshot_id: UUID, limit: int
) -> list[asyncpg.Record]:
    """The README's chunks, in file order, for the §30.5 overview fact group.

    **Only the README, and only at the repo root.** `docs/` is a whole corpus
    and would bury the graph facts it sits beside; a root `README*` is the one
    file a project writes for exactly the reader §19 is addressing.

    Returned with line ranges like every other fact group, because the point is
    that "How to run it" becomes citable rather than recalled — §19.3's rule
    that a fact you want cited has to arrive with something to cite.
    """
    return list(
        await conn.fetch(
            """
            SELECT file_path, header, code, start_line, end_line
              FROM chunks
             WHERE snapshot_id = $1
               AND is_prose
               AND kind = 'document'
               AND file_path NOT LIKE '%/%'
               AND upper(file_path) LIKE 'README%'
             ORDER BY file_path, start_line
             LIMIT $2
            """,
            snapshot_id,
            limit,
        )
    )


# --- §19 overview storage --------------------------------------------------


async def claim_overview(conn: asyncpg.Connection, snapshot_id: UUID) -> bool:
    """Try to become the generator for this snapshot's overview (§19.4).

    ``True`` means this caller inserted the row and owns the job. ``False``
    means somebody already holds it — generating, ready, or failed — and the
    caller should read the row rather than start a second model call.

    The primary key does the arbitration, so two simultaneous first views
    cannot both spend a request from a 20-per-day budget. No lock, no lease:
    the constraint is the mutual exclusion.
    """
    row = await conn.fetchrow(
        """
        INSERT INTO snapshot_overviews (snapshot_id, status)
        VALUES ($1, 'generating')
        ON CONFLICT (snapshot_id) DO NOTHING
        RETURNING snapshot_id
        """,
        snapshot_id,
    )
    return row is not None


async def get_overview(
    conn: asyncpg.Connection, snapshot_id: UUID
) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """SELECT snapshot_id, status, body, citations, model, error, created_at
             FROM snapshot_overviews WHERE snapshot_id = $1""",
        snapshot_id,
    )


async def finish_overview(
    conn: asyncpg.Connection,
    snapshot_id: UUID,
    *,
    body: str,
    citations: str,
    model: str,
) -> None:
    """Store a completed overview. ``citations`` is pre-serialised JSON."""
    await conn.execute(
        """
        UPDATE snapshot_overviews
           SET status = 'ready', body = $2, citations = $3::jsonb,
               model = $4, error = NULL
         WHERE snapshot_id = $1
        """,
        snapshot_id,
        body,
        citations,
        model,
    )


async def fail_overview(
    conn: asyncpg.Connection, snapshot_id: UUID, error: str
) -> None:
    """Record a failed generation. Truncated — this reaches a UI."""
    await conn.execute(
        "UPDATE snapshot_overviews SET status = 'failed', error = $2 WHERE snapshot_id = $1",
        snapshot_id,
        error[:2000],
    )


async def clear_failed_overview(conn: asyncpg.Connection, snapshot_id: UUID) -> bool:
    """Delete a ``failed`` row so a retry can claim it; ``True`` if one went.

    Deleting rather than resetting, so `claim_overview` stays the single place
    that decides who generates. A `failed` row that lingered would block every
    future attempt — the shape of the bug `010` had to fix for snapshots.
    """
    result = await conn.execute(
        "DELETE FROM snapshot_overviews WHERE snapshot_id = $1 AND status = 'failed'",
        snapshot_id,
    )
    return str(result).split()[-1] != "0"
