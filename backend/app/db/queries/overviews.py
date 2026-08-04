"""Generated repo overview storage (SPEC §19)."""

from __future__ import annotations

from uuid import UUID

import asyncpg

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
