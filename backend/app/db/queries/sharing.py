"""Shared answer permalinks (SPEC §21)."""

from __future__ import annotations

from uuid import UUID

import asyncpg

# --- §21 shared answer permalinks ------------------------------------------


async def create_shared_answer(
    conn: asyncpg.Connection,
    snapshot_id: UUID,
    user_id: UUID,
    *,
    question: str,
    answer: str,
    citations: str,
    model: str | None,
) -> UUID:
    """Publish one answer; return its permalink id.

    ``citations`` arrives as a JSON string already validated against this
    snapshot by the caller (§21.2) — this function stores, it does not vet.
    """
    row = await conn.fetchrow(
        """
        INSERT INTO shared_answers
          (snapshot_id, created_by, question, answer, citations, model)
        VALUES ($1, $2, $3, $4, $5::jsonb, $6)
        RETURNING id
        """,
        snapshot_id,
        user_id,
        question,
        answer,
        citations,
        model,
    )
    return UUID(str(row["id"]))


async def get_shared_answer(
    conn: asyncpg.Connection, share_id: UUID
) -> asyncpg.Record | None:
    """One published answer plus the repo facts its citations need.

    Joins through to the source so the public read can render citations as
    GitHub blob links at the pinned commit — the same trick 6.2's Markdown
    export uses, and the reason a permalink is useful to someone who does not
    have an account here.

    ``created_by`` is deliberately not selected: who published it is the
    owner's business, not the reader's.
    """
    return await conn.fetchrow(
        """
        SELECT sa.id, sa.question, sa.answer, sa.citations, sa.model,
               sa.created_at, sa.snapshot_id,
               s.name AS repo_name, s.url AS repo_url,
               sn.commit_sha, sn.strategy
          FROM shared_answers sa
          JOIN repo_snapshots sn ON sn.id = sa.snapshot_id
          JOIN repo_sources   s  ON s.id  = sn.source_id
         WHERE sa.id = $1
        """,
        share_id,
    )


async def delete_shared_answer(
    conn: asyncpg.Connection, share_id: UUID, user_id: UUID
) -> bool:
    """Unpublish. ``True`` if a row belonging to ``user_id`` was removed.

    Scoped to the publisher in the statement rather than checked first: a
    read-then-delete would be a race, and "not yours" and "not there" should be
    the same answer anyway (§13.5).
    """
    result = await conn.execute(
        "DELETE FROM shared_answers WHERE id = $1 AND created_by = $2",
        share_id,
        user_id,
    )
    return bool(result.endswith(" 1"))
