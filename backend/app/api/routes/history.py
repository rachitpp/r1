"""Commit history (SPEC §20)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query

from app.api.deps import Conn, CurrentUser
from app.api.routes._common import require_owned_repo
from app.api.schemas import (
    HistoryOut,
)
from app.config import (
    HISTORY_PAGE_MAX,
)
from app.db import queries

router = APIRouter()


@router.get("/repos/{snapshot_id}/history", response_model=HistoryOut)
async def get_repo_history(
    snapshot_id: UUID,
    conn: Conn,
    user: CurrentUser,
    path: str | None = Query(None),
    include_merges: bool = Query(False),
    limit: int = Query(HISTORY_PAGE_MAX, ge=1, le=HISTORY_PAGE_MAX),
) -> HistoryOut:
    """Commit history for a repo, or for one file in it (§20.2).

    **An endpoint, not a seventh agent tool.** §18.1 set the rule and this is
    the case it was written for: "what changed here recently" and "who last
    touched this" are exact answers a `WHERE` clause already knows, so routing
    them through the model would spend from a budget of 8 (§7.2) to compute
    something SQL has, and make it non-reproducible in the bargain. The half
    that *does* need judgement — reading a diff and explaining why — is not
    this endpoint.

    Like §18.3, an unknown ``path`` returns an empty list rather than 404, for
    the same existence-oracle reason. Unlike §18.3, the empty list is
    accompanied by ``indexed``, because here "we never looked" is a real and
    common state: no snapshot ingested before §20 has commit rows.
    """
    await require_owned_repo(conn, user["id"], snapshot_id)
    rows = await queries.commit_history(
        conn,
        snapshot_id,
        path=path,
        include_merges=include_merges,
        limit=limit,
    )
    # Only asked when the answer is empty — a non-empty list has already proved
    # history is indexed, and this would be a second round trip to learn it.
    indexed = bool(rows) or await queries.has_history(conn, snapshot_id)
    return HistoryOut.from_rows(
        rows,
        path=path,
        indexed=indexed,
        include_merges=include_merges,
        limit=limit,
    )
