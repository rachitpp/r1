"""Snapshot comparison (SPEC §28, FEATURE-IDEAS 6.3)."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

import asyncpg

# ---------------------------------------------------------------------------
# Snapshot comparison (SPEC §28, FEATURE-IDEAS 6.3)
# ---------------------------------------------------------------------------


async def compare_files(
    conn: asyncpg.Connection, base_id: UUID, head_id: UUID, limit: int
) -> tuple[list[str], list[str]]:
    """``(added, removed)`` file paths between two snapshots.

    A full outer join in one statement rather than two round trips and a set
    difference in Python: the answer is a property of the database's own view
    of both corpora, and computing it here keeps the two halves consistent even
    if a snapshot were being written while this ran.
    """
    rows = await conn.fetch(
        """
        SELECT COALESCE(h.path, b.path) AS path,
               (b.path IS NULL)         AS added
          FROM (SELECT path FROM files WHERE snapshot_id = $2) h
          FULL OUTER JOIN (SELECT path FROM files WHERE snapshot_id = $1) b
            ON b.path = h.path
         WHERE b.path IS NULL OR h.path IS NULL
         ORDER BY 1
         LIMIT $3
        """,
        base_id,
        head_id,
        limit,
    )
    added = [str(r["path"]) for r in rows if r["added"]]
    removed = [str(r["path"]) for r in rows if not r["added"]]
    return added, removed


async def compare_symbols(
    conn: asyncpg.Connection, base_id: UUID, head_id: UUID, limit: int
) -> tuple[list[asyncpg.Record], list[asyncpg.Record]]:
    """``(added, removed)`` symbols, keyed by qualname rather than by line.

    **Qualname, deliberately not (file, line).** Every symbol below an edit
    shifts by however many lines the edit added, so a line-keyed comparison
    reports an entire file as replaced because something near the top grew by
    two lines. Keying on the dotted name answers the question a reader is
    actually asking — *what is gone and what is new* — and treats a moved
    function as the same function, which it is.

    Tests are included: a deleted test is a real change to the corpus, and
    §6.3 is about what the snapshot contains rather than about coverage.
    """
    rows = await conn.fetch(
        """
        SELECT COALESCE(h.qualname, b.qualname) AS qualname,
               COALESCE(h.kind, b.kind)         AS kind,
               COALESCE(h.file_path, b.file_path) AS file_path,
               (b.qualname IS NULL)             AS added
          FROM (SELECT DISTINCT ON (qualname) qualname, kind, file_path
                  FROM symbols WHERE snapshot_id = $2 ORDER BY qualname) h
          FULL OUTER JOIN
               (SELECT DISTINCT ON (qualname) qualname, kind, file_path
                  FROM symbols WHERE snapshot_id = $1 ORDER BY qualname) b
            ON b.qualname = h.qualname
         WHERE b.qualname IS NULL OR h.qualname IS NULL
         ORDER BY 1
         LIMIT $3
        """,
        base_id,
        head_id,
        limit,
    )
    return [r for r in rows if r["added"]], [r for r in rows if not r["added"]]


async def compare_dependencies(
    conn: asyncpg.Connection, base_id: UUID, head_id: UUID, limit: int
) -> tuple[list[str], list[str]]:
    """``(added, removed)`` third-party packages between two snapshots (§26)."""
    rows = await conn.fetch(
        """
        SELECT COALESCE(h.module, b.module) AS module,
               (b.module IS NULL)           AS added
          FROM (SELECT DISTINCT module FROM dependency_uses
                 WHERE snapshot_id = $2 AND kind = 'third_party') h
          FULL OUTER JOIN
               (SELECT DISTINCT module FROM dependency_uses
                 WHERE snapshot_id = $1 AND kind = 'third_party') b
            ON b.module = h.module
         WHERE b.module IS NULL OR h.module IS NULL
         ORDER BY 1
         LIMIT $3
        """,
        base_id,
        head_id,
        limit,
    )
    added = [str(r["module"]) for r in rows if r["added"]]
    removed = [str(r["module"]) for r in rows if not r["added"]]
    return added, removed


async def commits_between(
    conn: asyncpg.Connection, base_id: UUID, head_id: UUID, limit: int
) -> list[asyncpg.Record]:
    """Commits present in ``head``'s history and absent from ``base``'s (§20).

    Empty when either snapshot predates the history pass, which the caller
    reports as "history was not indexed" rather than "nothing changed" — the
    §20.4 distinction, one level up.
    """
    return list(
        await conn.fetch(
            """
            SELECT sha, author_name, authored_at, subject
              FROM commits
             WHERE snapshot_id = $2
               AND sha NOT IN (SELECT sha FROM commits WHERE snapshot_id = $1)
             ORDER BY authored_at DESC
             LIMIT $3
            """,
            base_id,
            head_id,
            limit,
        )
    )


async def snapshot_meta(
    conn: asyncpg.Connection, ids: Sequence[UUID]
) -> dict[UUID, asyncpg.Record]:
    """``id -> (source_id, strategy, commit_sha, created_at)`` for §28.

    A dedicated read rather than widening ``SNAPSHOT_COLUMNS``: that tuple is
    the shape `RepoOut` is built from and every repo route returns, and adding
    two columns to it so one endpoint can compare a pair would push a detail of
    §28 into the response of everything else.
    """
    rows = await conn.fetch(
        """
        SELECT id, source_id, strategy, commit_sha, created_at
          FROM repo_snapshots
         WHERE id = ANY($1::uuid[])
        """,
        list(ids),
    )
    return {r["id"]: r for r in rows}


async def sibling_snapshots(
    conn: asyncpg.Connection, user_id: UUID, snapshot_id: UUID
) -> list[asyncpg.Record]:
    """Other snapshots of the same source that ``user_id`` can see (§28.3).

    Scoped by `user_repos` like everything else in §13.5, and to the same
    strategy — an `ast` corpus and a `naive` one are not comparable (§28.1), so
    offering the pairing in a picker would only produce a 400 on click.

    The snapshot itself is excluded: it is the thing being compared *from*.
    """
    return list(
        await conn.fetch(
            """
            SELECT sn.id, sn.commit_sha, sn.status, sn.created_at
              FROM repo_snapshots sn
              JOIN user_repos ur ON ur.snapshot_id = sn.id
             WHERE ur.user_id = $1
               AND sn.id <> $2
               AND sn.source_id = (
                     SELECT source_id FROM repo_snapshots WHERE id = $2
                   )
               AND sn.strategy = (
                     SELECT strategy FROM repo_snapshots WHERE id = $2
                   )
             ORDER BY sn.created_at DESC
             LIMIT 50
            """,
            user_id,
            snapshot_id,
        )
    )
