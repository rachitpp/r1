"""Users and their libraries (SPEC §13.2)."""

from __future__ import annotations

from uuid import UUID

import asyncpg

from app.config import get_settings

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
