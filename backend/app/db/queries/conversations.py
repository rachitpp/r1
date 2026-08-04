"""Persisted chat conversations (SPEC §23)."""

from __future__ import annotations

from uuid import UUID

import asyncpg

# --- §23 conversations -----------------------------------------------------


async def create_conversation(
    conn: asyncpg.Connection, snapshot_id: UUID, user_id: UUID, *, title: str
) -> UUID:
    """Open a conversation. ``title`` is the first question, already trimmed."""
    row = await conn.fetchrow(
        """
        INSERT INTO conversations (snapshot_id, user_id, title)
        VALUES ($1, $2, $3)
        RETURNING id
        """,
        snapshot_id,
        user_id,
        title,
    )
    return UUID(str(row["id"]))


async def owned_conversation(
    conn: asyncpg.Connection, conversation_id: UUID, user_id: UUID, snapshot_id: UUID
) -> asyncpg.Record | None:
    """The caller's conversation *for this snapshot*, or ``None``.

    All three predicates are load-bearing. Owner is §13.5. Snapshot is §23.1:
    a conversation's stored citations resolve against one immutable corpus, so
    replaying it against another would cite lines that no longer mean the same.
    """
    return await conn.fetchrow(
        """
        SELECT id, title, created_at, updated_at
          FROM conversations
         WHERE id = $1 AND user_id = $2 AND snapshot_id = $3
        """,
        conversation_id,
        user_id,
        snapshot_id,
    )


async def conversation_turns(
    conn: asyncpg.Connection, conversation_id: UUID, limit: int | None = None
) -> list[asyncpg.Record]:
    """Turns oldest-first. ``limit`` keeps the most *recent* ``limit`` of them.

    Recent rather than first: context for a follow-up is what was just said, and
    a window anchored at the start would drift further from the question with
    every turn. The result is still oldest-first, because that is the order a
    prompt and a transcript both want.
    """
    if limit is None:
        return list(
            await conn.fetch(
                """SELECT ordinal, question, answer, citations, created_at
                     FROM conversation_turns WHERE conversation_id = $1
                    ORDER BY ordinal""",
                conversation_id,
            )
        )
    return list(
        await conn.fetch(
            """
            SELECT * FROM (
                SELECT ordinal, question, answer, citations, created_at
                  FROM conversation_turns WHERE conversation_id = $1
                 ORDER BY ordinal DESC LIMIT $2
            ) recent ORDER BY ordinal
            """,
            conversation_id,
            limit,
        )
    )


async def append_turn(
    conn: asyncpg.Connection,
    conversation_id: UUID,
    *,
    question: str,
    answer: str,
    citations: str,
) -> int:
    """Store one completed turn; return its ordinal.

    The ordinal is computed in the same statement that inserts, so two turns
    racing on one conversation cannot both claim the same position — the
    primary key would reject the loser rather than silently interleaving.
    """
    row = await conn.fetchrow(
        """
        INSERT INTO conversation_turns
          (conversation_id, ordinal, question, answer, citations)
        SELECT $1,
               coalesce(max(ordinal), 0) + 1,
               $2, $3, $4::jsonb
          FROM conversation_turns WHERE conversation_id = $1
        RETURNING ordinal
        """,
        conversation_id,
        question,
        answer,
        citations,
    )
    await conn.execute(
        "UPDATE conversations SET updated_at = now() WHERE id = $1", conversation_id
    )
    return int(row["ordinal"])


async def list_conversations(
    conn: asyncpg.Connection, snapshot_id: UUID, user_id: UUID, limit: int
) -> list[asyncpg.Record]:
    """This user's conversations about this repo, most recently used first."""
    return list(
        await conn.fetch(
            """
            SELECT c.id, c.title, c.created_at, c.updated_at,
                   count(t.ordinal)::int AS n_turns
              FROM conversations c
              LEFT JOIN conversation_turns t ON t.conversation_id = c.id
             WHERE c.snapshot_id = $1 AND c.user_id = $2
             GROUP BY c.id, c.title, c.created_at, c.updated_at
             ORDER BY c.updated_at DESC
             LIMIT $3
            """,
            snapshot_id,
            user_id,
            limit,
        )
    )


async def delete_conversation(
    conn: asyncpg.Connection, conversation_id: UUID, user_id: UUID
) -> bool:
    """Delete a conversation and its turns. ``True`` if one was the caller's."""
    result = await conn.execute(
        "DELETE FROM conversations WHERE id = $1 AND user_id = $2",
        conversation_id,
        user_id,
    )
    return bool(result.endswith(" 1"))
