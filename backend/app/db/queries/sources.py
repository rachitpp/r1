"""Repo sources and snapshots: identity, creation, and lookup (SPEC §3, §10)."""

from __future__ import annotations

from uuid import UUID

import asyncpg

from app.db.queries._shared import (
    SNAPSHOT_COLUMNS,
    SNAPSHOT_FROM,
)


async def get_or_create_source(
    conn: asyncpg.Connection, *, url: str, name: str
) -> UUID:
    """Get-or-create the `repo_sources` row for ``url`` (SPEC §14.2).

    A source is the repo itself, independent of any commit or chunking
    strategy. It is created once and never rewritten: everything that varies —
    the commit, the status, the progress — belongs to a snapshot.
    """
    row = await conn.fetchrow(
        """
        INSERT INTO repo_sources (url, name) VALUES ($1, $2)
        ON CONFLICT (url) DO NOTHING
        RETURNING id
        """,
        url,
        name,
    )
    if row is not None:
        created: UUID = row["id"]
        return created
    existing = await conn.fetchrow("SELECT id FROM repo_sources WHERE url = $1", url)
    assert existing is not None  # the conflict we just hit proves it exists
    found: UUID = existing["id"]
    return found


async def create_snapshot(
    conn: asyncpg.Connection, source_id: UUID, *, strategy: str = "ast"
) -> UUID:
    """Open a new ``queued`` snapshot for ``source_id`` (§14.2).

    Always a fresh row. A snapshot is written once and frozen (§14.3), so a
    re-index is a *new* snapshot rather than a reset of the old one — which is
    the entire point of the phase: the corpus a reader is using is never the
    corpus a writer is rebuilding.
    """
    row = await conn.fetchrow(
        """
        INSERT INTO repo_snapshots (source_id, strategy, status)
        VALUES ($1, $2, 'queued')
        RETURNING id
        """,
        source_id,
        strategy,
    )
    assert row is not None
    new_id: UUID = row["id"]
    return new_id


# `commit_sha` is aliased to `head_sha` so `RepoOut.from_row` — and therefore
# the whole §8 contract and the frontend — is unchanged by the split (§14.7).

async def get_repo(conn: asyncpg.Connection, snapshot_id: UUID) -> asyncpg.Record | None:
    """One snapshot with its §8 ``RepoOut`` columns, or ``None``."""
    return await conn.fetchrow(
        f"SELECT {SNAPSHOT_COLUMNS} FROM {SNAPSHOT_FROM} WHERE sn.id = $1",
        snapshot_id,
    )


async def get_owned_repo(
    conn: asyncpg.Connection, user_id: UUID, snapshot_id: UUID
) -> asyncpg.Record | None:
    """``get_repo``, but only if ``user_id`` has it in their library (§13.5).

    Returning ``None`` for "exists but is not yours" is what lets the API answer
    404 rather than 403 — a 403 would confirm the id names a real repo, which is
    the fact being protected.
    """
    return await conn.fetchrow(
        f"""SELECT {SNAPSHOT_COLUMNS} FROM {SNAPSHOT_FROM}
             JOIN user_repos ur ON ur.snapshot_id = sn.id
            WHERE sn.id = $1 AND ur.user_id = $2""",
        snapshot_id,
        user_id,
    )


async def list_repos(
    conn: asyncpg.Connection, user_id: UUID | None = None
) -> list[asyncpg.Record]:
    """Snapshots, newest first — the caller's library when ``user_id`` is given.

    ``None`` means every snapshot and is for the CLIs and the worker, which act
    outside any user's session. No HTTP route may pass ``None`` (§13.6).
    """
    if user_id is None:
        rows = await conn.fetch(
            f"SELECT {SNAPSHOT_COLUMNS} FROM {SNAPSHOT_FROM} ORDER BY sn.created_at DESC"
        )
    else:
        rows = await conn.fetch(
            f"""SELECT {SNAPSHOT_COLUMNS} FROM {SNAPSHOT_FROM}
                 JOIN user_repos ur ON ur.snapshot_id = sn.id
                WHERE ur.user_id = $1
                ORDER BY sn.created_at DESC""",
            user_id,
        )
    return list(rows)


async def newest_snapshot_for_source(
    conn: asyncpg.Connection, source_id: UUID, *, strategy: str = "ast"
) -> asyncpg.Record | None:
    """The most recent snapshot for a source, whatever its status (§14.5).

    ``POST /repos`` decides what to do from this one row: a ``ready`` snapshot
    is returned as-is, a ``failed`` one is superseded by a new attempt, and an
    in-flight one is joined rather than duplicated.
    """
    return await conn.fetchrow(
        f"""SELECT {SNAPSHOT_COLUMNS} FROM {SNAPSHOT_FROM}
            WHERE sn.source_id = $1 AND sn.strategy = $2
            ORDER BY sn.created_at DESC LIMIT 1""",
        source_id,
        strategy,
    )


async def find_ready_snapshot(
    conn: asyncpg.Connection, source_id: UUID, commit_sha: str, strategy: str
) -> UUID | None:
    """A finished snapshot of exactly this commit, if one exists (§14.4).

    The worker's half of dedup, asked *after* the clone because the SHA is not
    knowable before it. This is where a thousand users submitting one popular
    repo collapses into a single ingest.
    """
    row = await conn.fetchrow(
        """SELECT id FROM repo_snapshots
            WHERE source_id = $1 AND commit_sha = $2 AND strategy = $3
              AND status = 'ready'
            LIMIT 1""",
        source_id,
        commit_sha,
        strategy,
    )
    return UUID(str(row["id"])) if row else None


async def source_of(conn: asyncpg.Connection, snapshot_id: UUID) -> asyncpg.Record | None:
    """``(source_id, url, name, strategy)`` for a snapshot — what the pipeline
    needs to clone, and what dedup needs to search on."""
    return await conn.fetchrow(
        """SELECT sn.source_id, s.url, s.name, sn.strategy
             FROM repo_snapshots sn JOIN repo_sources s ON s.id = sn.source_id
            WHERE sn.id = $1""",
        snapshot_id,
    )


async def supersede_snapshot(
    conn: asyncpg.Connection, redundant_id: UUID, keep_id: UUID
) -> None:
    """Move every library entry off a redundant snapshot, then delete it (§14.4).

    Runs when a clone reveals the commit is already ingested. The users who
    submitted it get the existing corpus — the whole saving — and the row that
    would have duplicated it goes away rather than lingering as a second copy
    nobody can tell apart from the first.
    """
    await conn.execute(
        """INSERT INTO user_repos (user_id, snapshot_id)
           SELECT user_id, $2 FROM user_repos WHERE snapshot_id = $1
           ON CONFLICT DO NOTHING""",
        redundant_id,
        keep_id,
    )
    await conn.execute("DELETE FROM repo_snapshots WHERE id = $1", redundant_id)
