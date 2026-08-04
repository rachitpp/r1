"""The ingest write path and its lifecycle: files, chunks, status, leases
(SPEC §10). Every function here runs one statement and owns no policy."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

import asyncpg

from app.db.queries._shared import (
    ACTIVE_STATUSES,
    IN_FLIGHT_STATUSES,
    SNAPSHOT_FROM,
    ChunkRow,
    FileRow,
)


async def get_file(
    conn: asyncpg.Connection, snapshot_id: UUID, path: str
) -> asyncpg.Record | None:
    """One stored file for the code viewer (§8 ``GET /repos/{id}/files``)."""
    return await conn.fetchrow(
        "SELECT path, content, n_lines FROM files WHERE snapshot_id = $1 AND path = $2",
        snapshot_id,
        path,
    )


async def start_ingest(
    conn: asyncpg.Connection, snapshot_id: UUID, *, status: str = "cloning"
) -> None:
    """Reset counters and clear any previous error at the start of a job (§10).

    A retry or a re-ingest must not inherit the previous run's progress numbers,
    or a failed attempt leaves a row claiming 1500 embedded chunks it no longer
    has.
    """
    await conn.execute(
        """
        UPDATE repo_snapshots
           SET status = $2, error = NULL,
               files_total = 0, files_parsed = 0,
               chunks_total = 0, chunks_embedded = 0,
               updated_at = now()
         WHERE id = $1
        """,
        snapshot_id,
        status,
    )


async def set_repo_status(
    conn: asyncpg.Connection, snapshot_id: UUID, status: str
) -> None:
    """Advance the §10 state machine, touching ``updated_at`` (zombie sweep)."""
    await conn.execute(
        "UPDATE repo_snapshots SET status = $2, updated_at = now() WHERE id = $1",
        snapshot_id,
        status,
    )


async def set_repo_clone_info(
    conn: asyncpg.Connection,
    snapshot_id: UUID,
    *,
    name: str,
    head_sha: str,
    default_branch: str,
) -> None:
    """Record what the clone resolved to. Known only after ``cloning`` (§10).

    Two rows, because the split put these facts in two places (§14.2): the
    commit and the branch describe *this* snapshot, while the name describes
    the source and is shared by every snapshot of it. ``name`` is refreshed
    because the source was created from the submitted URL alone; the clone is
    the first thing that has actually seen the repository.
    """
    await conn.execute(
        """
        UPDATE repo_snapshots
           SET commit_sha = $2, default_branch = $3, updated_at = now()
         WHERE id = $1
        """,
        snapshot_id,
        head_sha,
        default_branch,
    )
    await conn.execute(
        """
        UPDATE repo_sources SET name = $2
         WHERE id = (SELECT source_id FROM repo_snapshots WHERE id = $1)
        """,
        snapshot_id,
        name,
    )


async def set_repo_progress(
    conn: asyncpg.Connection,
    snapshot_id: UUID,
    *,
    files_total: int | None = None,
    files_parsed: int | None = None,
    chunks_total: int | None = None,
    chunks_embedded: int | None = None,
) -> None:
    """Write whichever progress counters the caller has (§10, batched writes).

    ``COALESCE`` on the parameter keeps unsupplied counters untouched, so the
    embed loop can report ``chunks_embedded`` every ``PROGRESS_EVERY_N`` units
    without restating the file counts it is no longer changing.
    """
    await conn.execute(
        """
        UPDATE repo_snapshots
           SET files_total     = COALESCE($2, files_total),
               files_parsed    = COALESCE($3, files_parsed),
               chunks_total    = COALESCE($4, chunks_total),
               chunks_embedded = COALESCE($5, chunks_embedded),
               updated_at = now()
         WHERE id = $1
        """,
        snapshot_id,
        files_total,
        files_parsed,
        chunks_total,
        chunks_embedded,
    )


async def fail_repo(conn: asyncpg.Connection, snapshot_id: UUID, error: str) -> None:
    """Record a job failure on the row (§10). Truncated — this reaches a UI."""
    await conn.execute(
        """
        UPDATE repo_snapshots
           SET status = 'failed', error = $2, updated_at = now()
         WHERE id = $1
        """,
        snapshot_id,
        error[:2000],
    )


async def sweep_zombie_repos(conn: asyncpg.Connection, older_than_s: int) -> list[str]:
    """Fail repos stuck in an in-flight state; return their ids (§10).

    Run on worker startup. A worker killed mid-ingest leaves its row claiming to
    be embedding forever: nothing else will ever touch it, because the job that
    owned it is gone. Time-based rather than worker-identity-based, which is why
    ``ZOMBIE_AFTER_S`` (1200s) is comfortably longer than ``job_timeout`` (900s)
    — a job that is merely slow must never be swept out from under itself.
    """
    rows = await conn.fetch(
        """
        UPDATE repo_snapshots
           SET status = 'failed', error = 'worker died', updated_at = now()
         WHERE status = ANY($1::text[])
           AND updated_at < now() - make_interval(secs => $2::double precision)
        RETURNING id
        """,
        list(IN_FLIGHT_STATUSES),
        float(older_than_s),
    )
    return [str(r["id"]) for r in rows]


async def claim_snapshot(
    conn: asyncpg.Connection, snapshot_id: UUID, worker: str
) -> bool:
    """Take the lease on a snapshot (SPEC §15.4). ``False`` if someone else has it.

    **The status transition is part of the claim**, not a separate step the
    pipeline does afterwards. Guarding on ``status = 'queued'`` while leaving the
    row queued makes the guard vacuous: two workers both match, both "win", and
    both ingest into the same snapshot — doubling every row. Moving it to
    ``cloning`` in the same statement is what makes exactly one UPDATE match.

    Claim, transition and first heartbeat are therefore one statement, so there
    is no instant where a row is claimed but looks stale, and none where it is
    claimable twice.
    """
    row = await conn.fetchrow(
        """
        UPDATE repo_snapshots
           SET status = 'cloning',
               claimed_by = $2, claimed_at = now(), heartbeat_at = now(),
               updated_at = now()
         WHERE id = $1 AND status = 'queued'
        RETURNING id
        """,
        snapshot_id,
        worker,
    )
    return row is not None


async def touch_heartbeat(conn: asyncpg.Connection, snapshot_id: UUID) -> None:
    """Refresh the lease (§15.4).

    Deliberately *not* touching ``updated_at``: that column means "the job made
    progress", and conflating it with "the worker is alive" is what left the old
    sweep unable to tell a silent-but-healthy `linking` phase from a dead one.
    """
    await conn.execute(
        "UPDATE repo_snapshots SET heartbeat_at = now() WHERE id = $1", snapshot_id
    )


async def sweep_expired_leases(
    conn: asyncpg.Connection, expiry_s: int
) -> list[str]:
    """Fail in-flight snapshots whose lease has gone stale; return their ids.

    Replaces the ``updated_at``-based sweep for rows that carry a lease. A row
    with no ``heartbeat_at`` is left alone here — it predates the lease columns,
    and :func:`sweep_zombie_repos` still covers it on the old timer, so neither
    path can reap what the other owns.
    """
    rows = await conn.fetch(
        """
        UPDATE repo_snapshots
           SET status = 'failed',
               error = 'worker lease expired',
               updated_at = now()
         WHERE status = ANY($1::text[])
           AND heartbeat_at IS NOT NULL
           AND heartbeat_at < now() - make_interval(secs => $2::double precision)
        RETURNING id
        """,
        list(IN_FLIGHT_STATUSES),
        float(expiry_s),
    )
    return [str(r["id"]) for r in rows]


async def count_active_ingests_for_user(
    conn: asyncpg.Connection, user_id: UUID
) -> int:
    """How many ingests *this user* has queued or running (§15.5).

    The global count it replaces meant one user's three queued repos refused
    everybody else's first submission — a per-user limit dressed up as capacity
    protection. Capacity is still bounded, by the size of the worker fleet.
    """
    count = await conn.fetchval(
        """
        SELECT count(*) FROM repo_snapshots sn
          JOIN user_repos ur ON ur.snapshot_id = sn.id
         WHERE ur.user_id = $1 AND sn.status = ANY($2::text[])
        """,
        user_id,
        list(ACTIVE_STATUSES),
    )
    return int(count or 0)


async def clear_repo_content(conn: asyncpg.Connection, snapshot_id: UUID) -> None:
    """Delete all files and chunks for ``snapshot_id`` (delete-and-replace, §10)."""
    await conn.execute("DELETE FROM chunks WHERE snapshot_id = $1", snapshot_id)
    await conn.execute("DELETE FROM files WHERE snapshot_id = $1", snapshot_id)


async def insert_files(
    conn: asyncpg.Connection, snapshot_id: UUID, files: Sequence[FileRow]
) -> int:
    """Batch-insert file rows; return the count inserted."""
    if not files:
        return 0
    await conn.executemany(
        """
        INSERT INTO files (snapshot_id, path, content, n_lines)
        VALUES ($1, $2, $3, $4)
        """,
        [(snapshot_id, path, content, n_lines) for path, content, n_lines in files],
    )
    return len(files)


async def insert_chunks(
    conn: asyncpg.Connection, snapshot_id: UUID, rows: Sequence[ChunkRow]
) -> int:
    """Batch-insert chunk rows (embedding last); return the count inserted."""
    if not rows:
        return 0
    await conn.executemany(
        """
        INSERT INTO chunks
          (snapshot_id, file_path, is_test, symbol, kind, part, n_parts,
           start_line, end_line, header, code, embedding)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
        """,
        [(snapshot_id, *row) for row in rows],
    )
    return len(rows)


async def finalize_repo(
    conn: asyncpg.Connection,
    snapshot_id: UUID,
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
        UPDATE repo_snapshots
           SET files_total = $2, files_parsed = $3,
               chunks_total = $4, chunks_embedded = $4,
               status = $5, updated_at = now()
         WHERE id = $1
        """,
        snapshot_id,
        files_total,
        files_parsed,
        chunks_total,
        status,
    )


async def resolve_snapshot_id(
    conn: asyncpg.Connection, ref: str, *, strategy: str = "ast"
) -> UUID | None:
    """Resolve a snapshot by its id, or by its source URL; ``None`` if neither.

    What the CLIs take on the command line. A URL now names a *source*, which
    may have several snapshots, so it resolves to the newest ready one of the
    requested strategy — the corpus someone typing a URL means. An id is exact
    and bypasses that entirely.
    """
    row = await conn.fetchrow(
        f"""SELECT sn.id FROM {SNAPSHOT_FROM}
             WHERE sn.id::text = $1
                OR (s.url = $1 AND sn.strategy = $2)
             ORDER BY (sn.id::text = $1) DESC,
                      (sn.status = 'ready') DESC,
                      sn.created_at DESC
             LIMIT 1""",
        ref,
        strategy,
    )
    if row is None:
        return None
    snapshot_id: UUID = row["id"]
    return snapshot_id


async def count_chunks(conn: asyncpg.Connection, snapshot_id: UUID) -> int:
    """Return the number of chunk rows stored for ``snapshot_id``."""
    value = await conn.fetchval("SELECT count(*) FROM chunks WHERE snapshot_id = $1", snapshot_id)
    return int(value)
