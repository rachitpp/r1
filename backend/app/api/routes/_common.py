"""Shared by more than one route module.

``chat_slots`` is module-level because it is a property of the *process*, not of
a request: it guards the cores, the inference threads, and the connection pool
every stream shares. ``ops`` reads its gauge; ``chat`` acquires from it. One
instance, imported by both — a second would silently double the real limit.
"""

from __future__ import annotations

from uuid import UUID

import asyncpg

from app.api.ratelimit import Slots
from app.config import (
    get_settings,
)
from app.db import queries
from app.exceptions import (
    RepoNotFoundError,
)

# Concurrent agent runs this process will accept.
chat_slots = Slots(get_settings().CHAT_MAX_CONCURRENCY)

# File contents are immutable for a given commit, so the browser may keep them
# for as long as it likes — the ETag names the commit, and a new commit is a new
# ETag rather than a stale one.
CACHE_IMMUTABLE = "private, max-age=31536000, immutable"


async def require_owned_repo(
    conn: asyncpg.Connection, user_id: UUID, snapshot_id: UUID
) -> asyncpg.Record:
    """The caller's repo, or 404 (SPEC §13.5).

    **This is the only place tenancy is enforced.** The six agent tools already
    scope every query by `snapshot_id`, so a route that resolved an *owned* repo
    makes everything downstream safe by construction; adding checks there too
    would be six more places to get wrong and would push a user identity into a
    layer with no other reason to know users exist.

    A repo that exists but belongs to someone else raises `RepoNotFoundError`,
    not an authorization error: 403 would confirm the id names a real repo,
    which is the fact being protected.
    """
    row = await queries.get_owned_repo(conn, user_id, snapshot_id)
    if row is None:
        raise RepoNotFoundError(snapshot_id)
    return row


