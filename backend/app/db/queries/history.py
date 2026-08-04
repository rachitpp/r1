"""Commit history (SPEC §20)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

import asyncpg

# --- §20 commit history ----------------------------------------------------


CommitRowT = tuple[str, str, str | None, datetime, str, str | None, bool]


async def insert_commits(
    conn: asyncpg.Connection, snapshot_id: UUID, rows: Sequence[CommitRowT]
) -> dict[str, int]:
    """Batch-insert commits; return ``sha -> commits.id`` for the file pass.

    ``RETURNING`` rather than a second SELECT: ids are database-assigned and
    ``commit_files`` needs them immediately, which is the same shape as the
    ``symbol_id_map`` step in the graph pass. ``ON CONFLICT DO NOTHING`` covers
    a re-run against a snapshot whose history is already stored — it cannot
    change, so re-inserting it is a no-op rather than an error.
    """
    if not rows:
        return {}
    records = await conn.fetch(
        """
        INSERT INTO commits
          (snapshot_id, sha, author_name, author_email, authored_at,
           subject, body, is_merge)
        SELECT $1, r.sha, r.author_name, r.author_email, r.authored_at,
               r.subject, r.body, r.is_merge
          FROM unnest($2::text[], $3::text[], $4::text[], $5::timestamptz[],
                      $6::text[], $7::text[], $8::bool[])
            AS r(sha, author_name, author_email, authored_at,
                 subject, body, is_merge)
        ON CONFLICT (snapshot_id, sha) DO NOTHING
        RETURNING id, sha
        """,
        snapshot_id,
        [r[0] for r in rows],
        [r[1] for r in rows],
        [r[2] for r in rows],
        [r[3] for r in rows],
        [r[4] for r in rows],
        [r[5] for r in rows],
        [r[6] for r in rows],
    )
    return {str(r["sha"]): int(r["id"]) for r in records}


CommitFileRowT = tuple[int, str, int, int]  # commit_id, path, insertions, deletions


async def insert_commit_files(
    conn: asyncpg.Connection, snapshot_id: UUID, rows: Sequence[CommitFileRowT]
) -> int:
    """Batch-insert per-file touches; return rows offered.

    Duplicates are possible when git reports one path twice in a single commit
    (a rename collapsing onto an existing name), so the primary key absorbs
    them rather than the caller de-duplicating.
    """
    if not rows:
        return 0
    await conn.executemany(
        """
        INSERT INTO commit_files
          (commit_id, snapshot_id, file_path, insertions, deletions)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (commit_id, file_path) DO NOTHING
        """,
        [(cid, snapshot_id, path, ins, dels) for cid, path, ins, dels in rows],
    )
    return len(rows)


async def clear_repo_history(conn: asyncpg.Connection, snapshot_id: UUID) -> None:
    """Delete commits (and, by cascade, commit_files) for ``snapshot_id``."""
    await conn.execute("DELETE FROM commits WHERE snapshot_id = $1", snapshot_id)


async def has_history(conn: asyncpg.Connection, snapshot_id: UUID) -> bool:
    """Whether history was indexed for this snapshot at all (§20.4).

    The distinction this exists to preserve: a snapshot ingested before §20
    has no commit rows, and so does a repo whose only commit is the initial
    one. Reporting both as an empty list would let "we never looked" read as
    "there is nothing to see" — the §18.3 empty-not-404 reasoning, one level
    up. ``EXISTS`` so the answer costs an index probe, not a count.
    """
    row = await conn.fetchrow(
        "SELECT EXISTS (SELECT 1 FROM commits WHERE snapshot_id = $1) AS present",
        snapshot_id,
    )
    return bool(row["present"]) if row else False


async def commit_history(
    conn: asyncpg.Connection,
    snapshot_id: UUID,
    *,
    path: str | None = None,
    include_merges: bool = False,
    limit: int = 100,
) -> list[asyncpg.Record]:
    """Reverse-chronological commits, optionally scoped to one file (§20.2).

    ``path`` scopes through ``commit_files``, which carries ``snapshot_id``
    itself so the filter is one index seek rather than a join that discards
    afterwards. Merges are excluded by default: a merge touches every file of
    the branch it absorbs, so on a per-file history it is noise, and §20.1
    stores the flag precisely so this stays a query-time decision.

    ``insertions``/``deletions`` are the deltas *for the requested path* when
    scoped, and the commit-wide totals when not — the number a reader would
    expect in each context.
    """
    if path is not None:
        return list(
            await conn.fetch(
                """
                SELECT c.sha, c.author_name, c.author_email, c.authored_at,
                       c.subject, c.body, c.is_merge,
                       cf.insertions, cf.deletions
                  FROM commit_files cf
                  JOIN commits c ON c.id = cf.commit_id
                 WHERE cf.snapshot_id = $1
                   AND cf.file_path = $2
                   AND ($3 OR NOT c.is_merge)
                 ORDER BY c.authored_at DESC, c.sha
                 LIMIT $4
                """,
                snapshot_id,
                path,
                include_merges,
                limit,
            )
        )
    return list(
        await conn.fetch(
            """
            SELECT c.sha, c.author_name, c.author_email, c.authored_at,
                   c.subject, c.body, c.is_merge,
                   COALESCE(SUM(cf.insertions), 0)::int AS insertions,
                   COALESCE(SUM(cf.deletions), 0)::int  AS deletions
              FROM commits c
              LEFT JOIN commit_files cf ON cf.commit_id = c.id
             WHERE c.snapshot_id = $1
               AND ($2 OR NOT c.is_merge)
             GROUP BY c.id, c.sha, c.author_name, c.author_email,
                      c.authored_at, c.subject, c.body, c.is_merge
             ORDER BY c.authored_at DESC, c.sha
             LIMIT $3
            """,
            snapshot_id,
            include_merges,
            limit,
        )
    )
