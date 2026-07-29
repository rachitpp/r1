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


async def create_repo(
    conn: asyncpg.Connection, *, url: str, name: str
) -> tuple[UUID, bool]:
    """Get-or-create the ``queued`` repo row for ``url``; return ``(id, created)``.

    ``url`` is UNIQUE (§3), and ``POST /repos`` distinguishes 201 from 200 on
    exactly this boolean. Nothing about an existing row is overwritten here —
    re-ingest resets the row (:func:`start_ingest`), and doing it at submit time
    would blank out a perfectly good ``ready`` repo before the job even starts.
    """
    row = await conn.fetchrow(
        """
        INSERT INTO repos (url, name, status)
        VALUES ($1, $2, 'queued')
        ON CONFLICT (url) DO NOTHING
        RETURNING id
        """,
        url,
        name,
    )
    if row is not None:
        created_id: UUID = row["id"]  # asyncpg decodes UUID columns to uuid.UUID
        return created_id, True
    existing = await conn.fetchrow("SELECT id FROM repos WHERE url = $1", url)
    assert existing is not None  # the conflict we just hit proves the row exists
    existing_id: UUID = existing["id"]
    return existing_id, False


REPO_COLUMNS = (
    "id, url, name, status, error, head_sha, default_branch, "
    "files_total, files_parsed, chunks_total, chunks_embedded, created_at"
)


async def get_repo(conn: asyncpg.Connection, repo_id: UUID) -> asyncpg.Record | None:
    """One repo row with its §8 ``RepoOut`` columns, or ``None``."""
    return await conn.fetchrow(
        f"SELECT {REPO_COLUMNS} FROM repos WHERE id = $1", repo_id
    )


async def get_owned_repo(
    conn: asyncpg.Connection, user_id: UUID, repo_id: UUID
) -> asyncpg.Record | None:
    """``get_repo``, but only if ``user_id`` has it in their library (§13.5).

    Returning ``None`` for "exists but is not yours" is what lets the API answer
    404 rather than 403 — a 403 would confirm the id names a real repo, which is
    the fact being protected.
    """
    return await conn.fetchrow(
        f"""SELECT {REPO_COLUMNS} FROM repos r
             JOIN user_repos ur ON ur.repo_id = r.id
            WHERE r.id = $1 AND ur.user_id = $2""",
        repo_id,
        user_id,
    )


async def list_repos(
    conn: asyncpg.Connection, user_id: UUID | None = None
) -> list[asyncpg.Record]:
    """Repo rows, newest first — the caller's library when ``user_id`` is given.

    ``None`` means every repo and is for the CLIs and the worker, which act
    outside any user's session. No HTTP route may pass ``None`` (§13.6).
    """
    if user_id is None:
        rows = await conn.fetch(
            f"SELECT {REPO_COLUMNS} FROM repos ORDER BY created_at DESC"
        )
    else:
        rows = await conn.fetch(
            f"""SELECT {REPO_COLUMNS} FROM repos r
                 JOIN user_repos ur ON ur.repo_id = r.id
                WHERE ur.user_id = $1
                ORDER BY r.created_at DESC""",
            user_id,
        )
    return list(rows)


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
    conn: asyncpg.Connection, user_id: UUID, repo_id: UUID
) -> None:
    """Put ``repo_id`` in ``user_id``'s library; idempotent (§13.6).

    A second user submitting a known URL joins the existing repo rather than
    re-ingesting it — the v1 schema already made a repo a singleton keyed by
    URL, and V2's snapshot split is what turns that from an accident into the
    design.
    """
    await conn.execute(
        """INSERT INTO user_repos (user_id, repo_id) VALUES ($1, $2)
           ON CONFLICT DO NOTHING""",
        user_id,
        repo_id,
    )


async def get_file(
    conn: asyncpg.Connection, repo_id: UUID, path: str
) -> asyncpg.Record | None:
    """One stored file for the code viewer (§8 ``GET /repos/{id}/files``)."""
    return await conn.fetchrow(
        "SELECT path, content, n_lines FROM files WHERE repo_id = $1 AND path = $2",
        repo_id,
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
        "SELECT count(*) FROM repos WHERE status = ANY($1::text[])",
        list(ACTIVE_STATUSES),
    )
    return int(value)


async def start_ingest(
    conn: asyncpg.Connection, repo_id: UUID, *, status: str = "cloning"
) -> None:
    """Reset counters and clear any previous error at the start of a job (§10).

    A retry or a re-ingest must not inherit the previous run's progress numbers,
    or a failed attempt leaves a row claiming 1500 embedded chunks it no longer
    has.
    """
    await conn.execute(
        """
        UPDATE repos
           SET status = $2, error = NULL,
               files_total = 0, files_parsed = 0,
               chunks_total = 0, chunks_embedded = 0,
               updated_at = now()
         WHERE id = $1
        """,
        repo_id,
        status,
    )


async def set_repo_status(
    conn: asyncpg.Connection, repo_id: UUID, status: str
) -> None:
    """Advance the §10 state machine, touching ``updated_at`` (zombie sweep)."""
    await conn.execute(
        "UPDATE repos SET status = $2, updated_at = now() WHERE id = $1",
        repo_id,
        status,
    )


async def set_repo_clone_info(
    conn: asyncpg.Connection,
    repo_id: UUID,
    *,
    name: str,
    head_sha: str,
    default_branch: str,
) -> None:
    """Record what the clone resolved to. Known only after ``cloning`` (§10).

    ``name`` is refreshed here because the row was created from the submitted
    URL alone; the clone is the first thing that has seen the actual repository.
    """
    await conn.execute(
        """
        UPDATE repos
           SET name = $2, head_sha = $3, default_branch = $4, updated_at = now()
         WHERE id = $1
        """,
        repo_id,
        name,
        head_sha,
        default_branch,
    )


async def set_repo_progress(
    conn: asyncpg.Connection,
    repo_id: UUID,
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
        UPDATE repos
           SET files_total     = COALESCE($2, files_total),
               files_parsed    = COALESCE($3, files_parsed),
               chunks_total    = COALESCE($4, chunks_total),
               chunks_embedded = COALESCE($5, chunks_embedded),
               updated_at = now()
         WHERE id = $1
        """,
        repo_id,
        files_total,
        files_parsed,
        chunks_total,
        chunks_embedded,
    )


async def fail_repo(conn: asyncpg.Connection, repo_id: UUID, error: str) -> None:
    """Record a job failure on the row (§10). Truncated — this reaches a UI."""
    await conn.execute(
        """
        UPDATE repos
           SET status = 'failed', error = $2, updated_at = now()
         WHERE id = $1
        """,
        repo_id,
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
        UPDATE repos
           SET status = 'failed', error = 'worker died', updated_at = now()
         WHERE status = ANY($1::text[])
           AND updated_at < now() - make_interval(secs => $2::double precision)
        RETURNING id
        """,
        list(IN_FLIGHT_STATUSES),
        float(older_than_s),
    )
    return [str(r["id"]) for r in rows]


async def clear_repo_content(conn: asyncpg.Connection, repo_id: UUID) -> None:
    """Delete all files and chunks for ``repo_id`` (delete-and-replace, §10)."""
    await conn.execute("DELETE FROM chunks WHERE repo_id = $1", repo_id)
    await conn.execute("DELETE FROM files WHERE repo_id = $1", repo_id)


async def insert_files(
    conn: asyncpg.Connection, repo_id: UUID, files: Sequence[FileRow]
) -> int:
    """Batch-insert file rows; return the count inserted."""
    if not files:
        return 0
    await conn.executemany(
        """
        INSERT INTO files (repo_id, path, content, n_lines)
        VALUES ($1, $2, $3, $4)
        """,
        [(repo_id, path, content, n_lines) for path, content, n_lines in files],
    )
    return len(files)


async def insert_chunks(
    conn: asyncpg.Connection, repo_id: UUID, rows: Sequence[ChunkRow]
) -> int:
    """Batch-insert chunk rows (embedding last); return the count inserted."""
    if not rows:
        return 0
    await conn.executemany(
        """
        INSERT INTO chunks
          (repo_id, file_path, is_test, symbol, kind, part, n_parts,
           start_line, end_line, header, code, embedding)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
        """,
        [(repo_id, *row) for row in rows],
    )
    return len(rows)


async def finalize_repo(
    conn: asyncpg.Connection,
    repo_id: UUID,
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
        UPDATE repos
           SET files_total = $2, files_parsed = $3,
               chunks_total = $4, chunks_embedded = $4,
               status = $5, updated_at = now()
         WHERE id = $1
        """,
        repo_id,
        files_total,
        files_parsed,
        chunks_total,
        status,
    )


async def resolve_repo_id(conn: asyncpg.Connection, ref: str) -> UUID | None:
    """Resolve a repo by exact URL or by id string; return its id or ``None``."""
    row = await conn.fetchrow(
        "SELECT id FROM repos WHERE url = $1 OR id::text = $1", ref
    )
    if row is None:
        return None
    repo_id: UUID = row["id"]
    return repo_id


async def count_chunks(conn: asyncpg.Connection, repo_id: UUID) -> int:
    """Return the number of chunk rows stored for ``repo_id``."""
    value = await conn.fetchval("SELECT count(*) FROM chunks WHERE repo_id = $1", repo_id)
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
    conn: asyncpg.Connection, repo_id: UUID, rows: Sequence[SymbolRowT]
) -> int:
    """Batch-insert symbol rows; return the count inserted."""
    if not rows:
        return 0
    await conn.executemany(
        """
        INSERT INTO symbols
          (repo_id, name, qualname, kind, file_path, start_line, end_line, is_test)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        """,
        [(repo_id, *row) for row in rows],
    )
    return len(rows)


async def symbol_id_map(
    conn: asyncpg.Connection, repo_id: UUID
) -> dict[tuple[str, int], int]:
    """Map ``(file_path, start_line)`` -> ``symbols.id`` for one repo.

    Extraction speaks in that natural key because ids are database-assigned;
    this is how edge rows get resolved to foreign keys after the symbol insert.
    """
    rows = await conn.fetch(
        "SELECT id, file_path, start_line FROM symbols WHERE repo_id = $1", repo_id
    )
    return {(str(r["file_path"]), int(r["start_line"])): int(r["id"]) for r in rows}


EdgeRowT = tuple[int, int, str, int | None]  # from_symbol, to_symbol, kind, line


async def insert_edges(
    conn: asyncpg.Connection, repo_id: UUID, rows: Sequence[EdgeRowT]
) -> int:
    """Batch-insert edge rows, ignoring duplicates; return rows offered.

    ``ON CONFLICT DO NOTHING`` covers the §3 unique key — the same call site
    can resolve to the same target twice across re-runs without erroring.
    """
    if not rows:
        return 0
    await conn.executemany(
        """
        INSERT INTO edges (repo_id, from_symbol, to_symbol, kind, line)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT DO NOTHING
        """,
        [(repo_id, *row) for row in rows],
    )
    return len(rows)


async def backfill_chunk_symbol_ids(conn: asyncpg.Connection, repo_id: UUID) -> int:
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
         WHERE c.repo_id = $1
           AND s.repo_id = $1
           AND c.part = 1
           AND c.file_path = s.file_path
           AND c.start_line = s.start_line
        """,
        repo_id,
    )
    return int(result.split()[-1]) if result else 0


async def clear_repo_graph(conn: asyncpg.Connection, repo_id: UUID) -> None:
    """Delete symbols (and, by cascade, edges) for ``repo_id``.

    Chunk ``symbol_id`` values are ``ON DELETE`` unconstrained, so null them
    first to avoid dangling references after a delete-and-replace re-ingest.
    """
    await conn.execute(
        "UPDATE chunks SET symbol_id = NULL WHERE repo_id = $1", repo_id
    )
    await conn.execute("DELETE FROM symbols WHERE repo_id = $1", repo_id)


async def resolve_symbol_id(
    conn: asyncpg.Connection,
    repo_id: UUID,
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

    The fallback keys on ``(repo_id, file_path, qualname)``: parts share both
    with their definition, and the pair is unique per definition in practice.
    """
    if symbol_id is not None:
        return symbol_id
    if not qualname:
        return None
    row = await conn.fetchrow(
        """
        SELECT id FROM symbols
         WHERE repo_id = $1 AND file_path = $2 AND qualname = $3
         ORDER BY start_line
         LIMIT 1
        """,
        repo_id,
        file_path,
        qualname,
    )
    return None if row is None else int(row["id"])


async def implementation_callers(
    conn: asyncpg.Connection, repo_id: UUID, symbol_id: int, limit: int
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
         WHERE e.repo_id = $1
           AND e.to_symbol = $2
           AND NOT s.is_test
         ORDER BY s.file_path, line
         LIMIT $3
        """,
        repo_id,
        symbol_id,
        limit,
    )
    total = await conn.fetchval(
        """
        SELECT count(*)
          FROM edges e JOIN symbols s ON s.id = e.from_symbol
         WHERE e.repo_id = $1 AND e.to_symbol = $2 AND NOT s.is_test
        """,
        repo_id,
        symbol_id,
    )
    return [(str(r["file_path"]), int(r["line"]), str(r["name"])) for r in rows], int(
        total
    )
