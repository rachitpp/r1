"""Shared answer permalinks (SPEC §21) — including this API's only
unauthenticated read."""

from __future__ import annotations

import json
import logging
from uuid import UUID

from fastapi import APIRouter, Response, status

from app.agent.citations import validate_citations
from app.api.deps import Conn, CurrentUser
from app.api.routes._common import require_owned_repo
from app.api.schemas import (
    ShareCreated,
    SharedAnswerOut,
    ShareRequest,
)
from app.db import queries
from app.exceptions import (
    SharedAnswerNotFoundError,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post(
    "/repos/{snapshot_id}/share",
    response_model=ShareCreated,
    status_code=status.HTTP_201_CREATED,
)
async def share_answer(
    snapshot_id: UUID,
    body: ShareRequest,
    conn: Conn,
    user: CurrentUser,
) -> ShareCreated:
    """Publish one answer under a permalink (§21.2).

    **Explicit, not automatic.** Chatting persists nothing server-side; a
    transcript lives in sessionStorage and dies there. Storing every answer
    would grow without bound, and quietly retaining someone's questions is a
    different decision from letting them publish one.

    **The citations are re-validated here.** They arrive from the client, and a
    client can send anything — a path from another repo, a range past EOF, a
    fabrication. `validate_citations` drops what is not in this snapshot and
    clamps what overruns it, so a permalink cannot be made to assert that some
    file says something it does not. This is the same function the agent's own
    output goes through (§7.5); the difference is that here the input is
    untrusted rather than merely unreliable.
    """
    await require_owned_repo(conn, user["id"], snapshot_id)
    citations = await validate_citations(
        conn,
        snapshot_id,
        [
            {
                "file_path": c.file_path,
                "start_line": c.start_line,
                "end_line": c.end_line,
            }
            for c in body.citations
        ],
    )
    share_id = await queries.create_shared_answer(
        conn,
        snapshot_id,
        user["id"],
        question=body.question,
        answer=body.answer,
        citations=json.dumps([dict(c) for c in citations]),
        model=body.model,
    )
    logger.info("shared answer %s for snapshot %s", share_id, snapshot_id)
    return ShareCreated(id=share_id)


@router.get("/shared/{share_id}", response_model=SharedAnswerOut)
async def get_shared_answer(share_id: UUID, conn: Conn) -> SharedAnswerOut:
    """Read a published answer. **No session required** (§21.3).

    This is the only route in the API that answers without an identity, and it
    is deliberate: a permalink nobody can open is not a permalink. The id is a
    random UUID, so knowing it *is* the authorization — the secret-link model.

    What that discloses — question, answer, cited paths and ranges, repo name,
    URL and commit — is in v1 entirely derived from a **public** GitHub
    repository. FEATURE-IDEAS 4.1 (private repos) must gate this route before a
    private corpus can exist; see the header of `013_shared_answers.sql`.

    404 for an unknown id, with no distinction between "never existed" and
    "unpublished" — §13.5's reasoning, and here it also means a retracted link
    stops confirming it was ever real.
    """
    row = await queries.get_shared_answer(conn, share_id)
    if row is None:
        raise SharedAnswerNotFoundError(share_id)
    return SharedAnswerOut.from_row(row)


@router.delete("/shared/{share_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unshare_answer(share_id: UUID, conn: Conn, user: CurrentUser) -> Response:
    """Retract a permalink. Only the publisher can (§21.4).

    A feature that mints public links needs an undo, and it needs to be the
    same shape as the 404 above: "not yours" and "not there" are one answer, so
    this cannot be used to probe which ids exist.
    """
    if not await queries.delete_shared_answer(conn, share_id, user["id"]):
        raise SharedAnswerNotFoundError(share_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
