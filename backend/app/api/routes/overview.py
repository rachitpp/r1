"""The generated repo overview (SPEC §19)."""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Query, Response, status

from app.api.deps import Arq, Conn, CurrentUser
from app.api.routes._common import require_owned_repo
from app.api.schemas import (
    OverviewOut,
)
from app.db import queries
from app.exceptions import (
    RepoNotFoundError,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/repos/{snapshot_id}/overview", response_model=OverviewOut)
async def get_repo_overview(
    snapshot_id: UUID,
    response: Response,
    conn: Conn,
    arq: Arq,
    user: CurrentUser,
    retry: bool = Query(False),
) -> OverviewOut:
    """The generated "start here" guide (§19.4). Generates on first view.

    **Lazily, not at the end of ingest.** Two reasons, and the second is the one
    that decided it: generation would add a model call to the critical path of
    every ingest including the ones nobody ever opens, and a lazy path gives an
    overview to snapshots that were ingested before this feature existed —
    which is every snapshot currently in the database.

    The model call itself is on the queue, never here (CLAUDE.md rule 1 in
    spirit: a handler that blocks for tens of seconds holds a connection for all
    of them). This claims the row and returns **202**; the worker fills it in
    and a later request gets **200**.

    Concurrency is settled by the primary key, not a lock: two browsers opening
    the same repo both attempt the insert, exactly one wins, and only that one
    enqueues. On a 20-request-per-day tier that difference is the feature.
    """
    await require_owned_repo(conn, user["id"], snapshot_id)

    # A failed row would otherwise block every future attempt — the shape of the
    # bug `010` fixed for snapshots. Clearing it is the whole retry.
    if retry:
        await queries.clear_failed_overview(conn, snapshot_id)

    if await queries.claim_overview(conn, snapshot_id):
        await arq.enqueue_job("generate_overview", str(snapshot_id))
        logger.info("enqueued overview for snapshot %s", snapshot_id)

    row = await queries.get_overview(conn, snapshot_id)
    if row is None:  # pragma: no cover — the claim above guarantees a row
        raise RepoNotFoundError(snapshot_id)
    if row["status"] == "generating":
        response.status_code = status.HTTP_202_ACCEPTED
    return OverviewOut.from_row(row)
