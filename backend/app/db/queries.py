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

from app.config import get_settings

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


# SPEC §10 state machine:
#   queued -> cloning -> parsing -> linking -> embedding -> ready | failed
#
# IN_FLIGHT_STATUSES is what the zombie sweep considers abandoned work. It
# excludes ``queued`` deliberately: a queued repo's job lives in Redis, which
# redelivers it when a worker returns, so a long queue wait is not a zombie.
# Only states a worker enters *while holding* the job can be orphaned by its
# death.
REPO_STATUSES: tuple[str, ...] = (
    "queued",
    "cloning",
    "parsing",
    "linking",
    "embedding",
    "ready",
    "failed",
)
IN_FLIGHT_STATUSES: tuple[str, ...] = ("cloning", "parsing", "linking", "embedding")


STRATEGIES: tuple[str, ...] = ("ast", "naive")


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
SNAPSHOT_COLUMNS = (
    "sn.id, s.url, s.name, sn.status, sn.error, sn.commit_sha AS head_sha, "
    "sn.default_branch, sn.files_total, sn.files_parsed, sn.chunks_total, "
    "sn.chunks_embedded, sn.created_at"
)
SNAPSHOT_FROM = "repo_snapshots sn JOIN repo_sources s ON s.id = sn.source_id"


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


# ---------------------------------------------------------------------------
# Users and libraries (SPEC §13.2)
# ---------------------------------------------------------------------------

USER_COLUMNS = "id, github_id, login, name, avatar_url, created_at"


async def upsert_user(
    conn: asyncpg.Connection,
    *,
    github_id: int,
    login: str,
    name: str | None,
    avatar_url: str | None,
) -> asyncpg.Record:
    """Get-or-create the user for ``github_id``, refreshing their profile.

    Keyed on ``github_id``, never ``login`` (§13.2): GitHub accounts can be
    renamed, and keying on the mutable name would strand the old row's library.
    This is also what adopts the §13.7 bootstrap row — the operator sets
    ``BOOTSTRAP_GITHUB_ID`` to their own id, and their first sign-in updates
    that row in place rather than creating a second one, inheriting every
    pre-auth repo with it.
    """
    row = await conn.fetchrow(
        f"""INSERT INTO users (github_id, login, name, avatar_url)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (github_id) DO UPDATE
              SET login = EXCLUDED.login,
                  name = EXCLUDED.name,
                  avatar_url = EXCLUDED.avatar_url,
                  last_seen_at = now()
            RETURNING {USER_COLUMNS}""",
        github_id,
        login,
        name,
        avatar_url,
    )
    assert row is not None  # INSERT ... RETURNING always yields a row here
    return row


async def get_user(conn: asyncpg.Connection, user_id: UUID) -> asyncpg.Record | None:
    """One user row by internal id (the session token's subject)."""
    return await conn.fetchrow(
        f"SELECT {USER_COLUMNS} FROM users WHERE id = $1", user_id
    )


async def adopt_bootstrap_user(conn: asyncpg.Connection, github_id: int) -> None:
    """Hand the §13.7 placeholder's identity to a real account, once.

    Runs before the sign-in upsert. A no-op unless the placeholder still exists
    and the real account has never signed in — if both rows exist they are
    already distinct users, and merging libraries silently would be a surprise
    rather than a migration.
    """
    await conn.execute(
        """UPDATE users SET github_id = $1
            WHERE github_id = 0
              AND NOT EXISTS (SELECT 1 FROM users WHERE github_id = $1)""",
        github_id,
    )


async def resolve_owner_id(
    conn: asyncpg.Connection, login: str | None = None
) -> UUID | None:
    """Which user a CLI ingest should hand its repo to (§13.5).

    ``login`` names a user explicitly. Without one, fall back to the operator
    identified by ``BOOTSTRAP_GITHUB_ID`` — the same account §13.7 hands the
    pre-auth repos to, so a CLI ingest lands in the same library as everything
    else the operator owns.

    ``None`` means there is nobody to give it to, and the caller must say so
    rather than write an unreachable row: a repo with no `user_repos` entry is
    invisible to `GET /repos` and 404s on every route, for everyone.
    """
    if login is not None:
        row = await conn.fetchrow("SELECT id FROM users WHERE login = $1", login)
        return UUID(str(row["id"])) if row else None

    github_id = get_settings().BOOTSTRAP_GITHUB_ID
    if github_id is None:
        return None
    row = await conn.fetchrow(
        "SELECT id FROM users WHERE github_id = $1", github_id
    )
    return UUID(str(row["id"])) if row else None


async def link_user_repo(
    conn: asyncpg.Connection, user_id: UUID, snapshot_id: UUID
) -> None:
    """Put ``snapshot_id`` in ``user_id``'s library; idempotent (§13.6).

    A second user submitting a known URL joins the existing repo rather than
    re-ingesting it — the v1 schema already made a repo a singleton keyed by
    URL, and V2's snapshot split is what turns that from an accident into the
    design.
    """
    await conn.execute(
        """INSERT INTO user_repos (user_id, snapshot_id) VALUES ($1, $2)
           ON CONFLICT DO NOTHING""",
        user_id,
        snapshot_id,
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


# Everything a worker will get to without anyone submitting anything further.
# ``queued`` is included here and excluded from the zombie sweep, for the same
# reason in both cases: a queued job is real work that exists, it just has not
# started.
ACTIVE_STATUSES: tuple[str, ...] = ("queued", *IN_FLIGHT_STATUSES)


async def count_active_ingests(conn: asyncpg.Connection) -> int:
    """How many repos are queued or mid-ingest right now.

    ``POST /repos`` refuses past ``MAX_ACTIVE_INGESTS``: each ingest is minutes
    of tree-sitter, Jedi, and embedding on the same box that serves chat, and a
    queue nobody bounded is just a slower way to run out of machine.
    """
    value = await conn.fetchval(
        "SELECT count(*) FROM repo_snapshots WHERE status = ANY($1::text[])",
        list(ACTIVE_STATUSES),
    )
    return int(value)


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


# --- Phase 3: symbol graph (SPEC §3, §6) -----------------------------------

SymbolRowT = tuple[
    str,  # name
    str,  # qualname
    str,  # kind
    str,  # file_path
    int,  # start_line
    int,  # end_line
    bool,  # is_test
]


async def insert_symbols(
    conn: asyncpg.Connection, snapshot_id: UUID, rows: Sequence[SymbolRowT]
) -> int:
    """Batch-insert symbol rows; return the count inserted."""
    if not rows:
        return 0
    await conn.executemany(
        """
        INSERT INTO symbols
          (snapshot_id, name, qualname, kind, file_path, start_line, end_line, is_test)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        """,
        [(snapshot_id, *row) for row in rows],
    )
    return len(rows)


async def symbol_id_map(
    conn: asyncpg.Connection, snapshot_id: UUID
) -> dict[tuple[str, int], int]:
    """Map ``(file_path, start_line)`` -> ``symbols.id`` for one repo.

    Extraction speaks in that natural key because ids are database-assigned;
    this is how edge rows get resolved to foreign keys after the symbol insert.
    """
    rows = await conn.fetch(
        "SELECT id, file_path, start_line FROM symbols WHERE snapshot_id = $1", snapshot_id
    )
    return {(str(r["file_path"]), int(r["start_line"])): int(r["id"]) for r in rows}


EdgeRowT = tuple[int, int, str, int | None]  # from_symbol, to_symbol, kind, line


async def insert_edges(
    conn: asyncpg.Connection, snapshot_id: UUID, rows: Sequence[EdgeRowT]
) -> int:
    """Batch-insert edge rows, ignoring duplicates; return rows offered.

    ``ON CONFLICT DO NOTHING`` covers the §3 unique key — the same call site
    can resolve to the same target twice across re-runs without erroring.
    """
    if not rows:
        return 0
    await conn.executemany(
        """
        INSERT INTO edges (snapshot_id, from_symbol, to_symbol, kind, line)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT DO NOTHING
        """,
        [(snapshot_id, *row) for row in rows],
    )
    return len(rows)


async def backfill_chunk_symbol_ids(conn: asyncpg.Connection, snapshot_id: UUID) -> int:
    """Link chunks to their defining symbol; return the number linked.

    Joins on ``(file_path, start_line)`` and is **restricted to ``part = 1``**.

    An oversize chunk's later parts carry a shifted ``start_line`` (SPEC §2.5)
    that can land exactly on a *different* symbol's start line — measured on
    httpx: 6 parts mislinked to the `__init__` they happened to begin at.
    Parts 2..n therefore stay NULL rather than pointing at the wrong symbol; a
    wrong link is worse than a missing one, and part 1 already carries the
    definition's identity.
    """
    result = await conn.execute(
        """
        UPDATE chunks c
           SET symbol_id = s.id
          FROM symbols s
         WHERE c.snapshot_id = $1
           AND s.snapshot_id = $1
           AND c.part = 1
           AND c.file_path = s.file_path
           AND c.start_line = s.start_line
        """,
        snapshot_id,
    )
    return int(result.split()[-1]) if result else 0


async def clear_repo_graph(conn: asyncpg.Connection, snapshot_id: UUID) -> None:
    """Delete symbols (and, by cascade, edges) for ``snapshot_id``.

    Chunk ``symbol_id`` values are ``ON DELETE`` unconstrained, so null them
    first to avoid dangling references after a delete-and-replace re-ingest.
    """
    await conn.execute(
        "UPDATE chunks SET symbol_id = NULL WHERE snapshot_id = $1", snapshot_id
    )
    await conn.execute("DELETE FROM symbols WHERE snapshot_id = $1", snapshot_id)


async def resolve_symbol_id(
    conn: asyncpg.Connection,
    snapshot_id: UUID,
    *,
    symbol_id: int | None,
    file_path: str,
    qualname: str | None,
) -> int | None:
    """Symbol id for a chunk, falling back for oversize parts 2..n.

    ``backfill_chunk_symbol_ids`` only links ``part = 1``, so a multi-part
    function's later parts carry ``symbol_id IS NULL`` by design. Every
    consumer that joins through ``symbol_id`` — §7.4 called-by assembly in
    particular — must come through here, or a long function silently loses its
    caller annotations on every part but the first.

    The fallback keys on ``(snapshot_id, file_path, qualname)``: parts share both
    with their definition, and the pair is unique per definition in practice.
    """
    if symbol_id is not None:
        return symbol_id
    if not qualname:
        return None
    row = await conn.fetchrow(
        """
        SELECT id FROM symbols
         WHERE snapshot_id = $1 AND file_path = $2 AND qualname = $3
         ORDER BY start_line
         LIMIT 1
        """,
        snapshot_id,
        file_path,
        qualname,
    )
    return None if row is None else int(row["id"])


async def implementation_callers(
    conn: asyncpg.Connection, snapshot_id: UUID, symbol_id: int, limit: int
) -> tuple[list[tuple[str, int, str]], int]:
    """Incoming call/import edges from **implementation** symbols (SPEC §7.4).

    Returns ``(rows, total)`` where each row is ``(file_path, line, name)`` and
    ``total`` is the unclipped count, so the caller can render "+N more".
    Test-side callers are excluded (§6.3): on a well-tested repo they outnumber
    the real call sites and would bury the answer.
    """
    rows = await conn.fetch(
        """
        SELECT s.file_path, COALESCE(e.line, s.start_line) AS line, s.name
          FROM edges e
          JOIN symbols s ON s.id = e.from_symbol
         WHERE e.snapshot_id = $1
           AND e.to_symbol = $2
           AND NOT s.is_test
         ORDER BY s.file_path, line
         LIMIT $3
        """,
        snapshot_id,
        symbol_id,
        limit,
    )
    total = await conn.fetchval(
        """
        SELECT count(*)
          FROM edges e JOIN symbols s ON s.id = e.from_symbol
         WHERE e.snapshot_id = $1 AND e.to_symbol = $2 AND NOT s.is_test
        """,
        snapshot_id,
        symbol_id,
    )
    return [(str(r["file_path"]), int(r["line"]), str(r["name"])) for r in rows], int(
        total
    )


# --- §18 graph views: module rollup and test linkage -----------------------
#
# Read-only aggregations over the *existing* symbol graph. No new extraction,
# no ingest change, and — deliberately — no new agent tool: the answers here are
# deterministic SQL, so routing them through the model would spend from the
# eight-call budget (§7.2) to compute something a query already knows exactly.
#
# In Python a file *is* a module, so `symbols.file_path` is the module key
# directly rather than a derived package string. Nothing is parsed out of the
# path, which means the rollup cannot disagree with the graph it summarises.
#
# `include_tests` follows §6.3 flag-and-filter: extraction kept every symbol,
# the decision happens here, and the counterfactual stays one parameter away.


async def module_nodes(
    conn: asyncpg.Connection,
    snapshot_id: UUID,
    *,
    include_tests: bool,
    limit: int,
) -> list[tuple[str, int, int, int]]:
    """Modules ranked by fan-in: ``(path, n_symbols, fan_in, fan_out)``.

    Fan-in counts edges arriving from *other* files, which is the closest thing
    the graph has to "how much of this repo depends on this module". Same-file
    edges are excluded on both counts: a module calling itself says nothing
    about the architecture, and on a large file it would dominate the ranking.

    Ordered by fan-in with ``file_path`` as the tiebreaker — the 2026-07-29
    tie-ordering fix applies here too, or the truncation at ``limit`` would pick
    a different top-N per physical row order.
    """
    rows = await conn.fetch(
        """
        WITH scoped AS (
            SELECT id, file_path
              FROM symbols
             WHERE snapshot_id = $1
               AND (NOT is_test OR $2)
        ),
        cross_edges AS (
            SELECT f.file_path AS from_path, t.file_path AS to_path
              FROM edges e
              JOIN scoped f ON f.id = e.from_symbol
              JOIN scoped t ON t.id = e.to_symbol
             WHERE e.snapshot_id = $1
               AND f.file_path <> t.file_path
        )
        SELECT s.file_path AS path,
               count(*) AS n_symbols,
               (SELECT count(*) FROM cross_edges c WHERE c.to_path = s.file_path)
                 AS fan_in,
               (SELECT count(*) FROM cross_edges c WHERE c.from_path = s.file_path)
                 AS fan_out
          FROM scoped s
         GROUP BY s.file_path
         ORDER BY fan_in DESC, s.file_path
         LIMIT $3
        """,
        snapshot_id,
        include_tests,
        limit,
    )
    return [
        (str(r["path"]), int(r["n_symbols"]), int(r["fan_in"]), int(r["fan_out"]))
        for r in rows
    ]


async def module_edges(
    conn: asyncpg.Connection,
    snapshot_id: UUID,
    *,
    include_tests: bool,
    limit: int,
) -> list[tuple[str, str, str, int]]:
    """Module-to-module edges: ``(from_path, to_path, kind, weight)``.

    ``weight`` is how many symbol-level edges of that kind cross the pair, which
    is what lets a renderer draw a thick line for "these two modules are deeply
    coupled" and a thin one for a single import.
    """
    rows = await conn.fetch(
        """
        WITH scoped AS (
            SELECT id, file_path
              FROM symbols
             WHERE snapshot_id = $1
               AND (NOT is_test OR $2)
        )
        SELECT f.file_path AS from_path,
               t.file_path AS to_path,
               e.kind      AS kind,
               count(*)    AS weight
          FROM edges e
          JOIN scoped f ON f.id = e.from_symbol
          JOIN scoped t ON t.id = e.to_symbol
         WHERE e.snapshot_id = $1
           AND f.file_path <> t.file_path
         GROUP BY f.file_path, t.file_path, e.kind
         ORDER BY weight DESC, from_path, to_path, kind
         LIMIT $3
        """,
        snapshot_id,
        include_tests,
        limit,
    )
    return [
        (str(r["from_path"]), str(r["to_path"]), str(r["kind"]), int(r["weight"]))
        for r in rows
    ]


async def tests_covering_file(
    conn: asyncpg.Connection, snapshot_id: UUID, file_path: str, limit: int
) -> list[asyncpg.Record]:
    """Test symbols with an edge into each symbol defined in ``file_path``.

    The mirror of :func:`implementation_callers`, which excludes the test side
    precisely because it is noise when the question is "who uses this?". Here
    the test side *is* the question, so the filter is inverted rather than
    dropped — a caller from another implementation file is not coverage.

    Flat rows, one per (symbol, test) pair; the response model groups them. Both
    join sides are covered by ``edges_to`` and ``symbols_snapshot_name``.
    """
    return list(
        await conn.fetch(
            """
            SELECT impl.name       AS name,
                   impl.qualname   AS qualname,
                   impl.kind       AS kind,
                   impl.start_line AS start_line,
                   impl.end_line   AS end_line,
                   t.qualname      AS ref_qualname,
                   t.file_path     AS ref_file_path,
                   COALESCE(e.line, t.start_line) AS ref_line
              FROM symbols impl
              JOIN edges   e ON e.to_symbol = impl.id AND e.snapshot_id = $1
              JOIN symbols t ON t.id = e.from_symbol
             WHERE impl.snapshot_id = $1
               AND impl.file_path = $2
               AND t.is_test
             ORDER BY impl.start_line, t.file_path, ref_line, t.qualname
             LIMIT $3
            """,
            snapshot_id,
            file_path,
            limit,
        )
    )


async def implementation_covered_by_file(
    conn: asyncpg.Connection, snapshot_id: UUID, file_path: str, limit: int
) -> list[asyncpg.Record]:
    """What the *test* symbols in ``file_path`` reach in implementation code.

    The reverse direction of :func:`tests_covering_file`. Empty for a file that
    defines no test symbols, which is the correct answer rather than a special
    case: an implementation file does not "cover" anything.
    """
    return list(
        await conn.fetch(
            """
            SELECT DISTINCT
                   impl.qualname  AS ref_qualname,
                   impl.file_path AS ref_file_path,
                   COALESCE(e.line, impl.start_line) AS ref_line
              FROM symbols t
              JOIN edges   e    ON e.from_symbol = t.id AND e.snapshot_id = $1
              JOIN symbols impl ON impl.id = e.to_symbol
             WHERE t.snapshot_id = $1
               AND t.file_path = $2
               AND t.is_test
               AND NOT impl.is_test
             ORDER BY ref_file_path, ref_line, ref_qualname
             LIMIT $3
            """,
            snapshot_id,
            file_path,
            limit,
        )
    )
