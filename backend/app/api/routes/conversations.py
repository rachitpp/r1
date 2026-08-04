"""Persisted chat conversations (SPEC §23)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Response, status

from app.api.deps import Conn, CurrentUser
from app.api.routes._common import require_owned_repo
from app.api.schemas import (
    ConversationDetail,
    ConversationList,
    ConversationOut,
)
from app.config import (
    CONVERSATION_PAGE_MAX,
)
from app.db import queries
from app.exceptions import (
    ConversationNotFoundError,
)

router = APIRouter()


@router.get(
    "/repos/{snapshot_id}/conversations", response_model=ConversationList
)
async def list_repo_conversations(
    snapshot_id: UUID, conn: Conn, user: CurrentUser
) -> ConversationList:
    """This caller's conversations about this repo, most recently used first (§23.4)."""
    await require_owned_repo(conn, user["id"], snapshot_id)
    rows = await queries.list_conversations(
        conn, snapshot_id, user["id"], CONVERSATION_PAGE_MAX
    )
    return ConversationList(
        conversations=[ConversationOut(**dict(r)) for r in rows]
    )


@router.get(
    "/repos/{snapshot_id}/conversations/{conversation_id}",
    response_model=ConversationDetail,
)
async def get_repo_conversation(
    snapshot_id: UUID, conversation_id: UUID, conn: Conn, user: CurrentUser
) -> ConversationDetail:
    """Everything needed to resume one conversation (§23.4).

    Every turn carries its stored, already-validated citations, so resuming
    renders a full transcript with no model call and no re-validation — which is
    the whole reason turns are stored rather than replayed.
    """
    await require_owned_repo(conn, user["id"], snapshot_id)
    convo = await queries.owned_conversation(
        conn, conversation_id, user["id"], snapshot_id
    )
    if convo is None:
        raise ConversationNotFoundError(conversation_id)
    turns = await queries.conversation_turns(conn, conversation_id)
    return ConversationDetail.from_rows(convo, turns)


@router.delete(
    "/repos/{snapshot_id}/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_repo_conversation(
    snapshot_id: UUID, conversation_id: UUID, conn: Conn, user: CurrentUser
) -> Response:
    """Forget a conversation. Scoped in the statement, like §21.4."""
    await require_owned_repo(conn, user["id"], snapshot_id)
    if not await queries.delete_conversation(conn, conversation_id, user["id"]):
        raise ConversationNotFoundError(conversation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
