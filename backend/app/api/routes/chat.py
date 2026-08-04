"""The §9 SSE chat stream: the one route that runs an agent loop."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

from app import metrics
from app.api.chat_stream import chat_event_stream
from app.api.deps import ChatModel, CurrentUser, Pool
from app.api.routes._common import chat_slots, require_owned_repo
from app.api.schemas import (
    ChatRequest,
)
from app.config import (
    CONVERSATION_CONTEXT_TURNS,
)
from app.db import queries
from app.db.pool import acquire
from app.exceptions import (
    ConversationNotFoundError,
    RepoNotReadyError,
    ServiceBusyError,
)

router = APIRouter()


@router.post("/repos/{snapshot_id}/chat")
async def chat(
    snapshot_id: UUID,
    body: ChatRequest,
    pool: Pool,
    model: ChatModel,
    user: CurrentUser,
) -> EventSourceResponse:
    """Stream an agent answer as §9 SSE events (SSE only — hard rule 7).

    Takes the **pool**, not a connection. An agent run lasts as long as the model
    does; a connection checked out for that whole span is one no other request
    can use, and ``CHAT_MAX_CONCURRENCY`` of those would starve the rest of the
    API. The graph borrows one per tool call instead.

    The slot is taken here and released by the stream, because a 429 has to be
    decided while a status code can still be sent — once ``EventSourceResponse``
    is returned, the only thing left to say is an ``error`` event.
    """
    history: list[Any] = []
    conversation_id = body.conversation_id
    async with acquire(pool) as conn:
        row = await require_owned_repo(conn, user["id"], snapshot_id)
        if row["status"] != "ready":
            raise RepoNotReadyError(str(row["status"]))
        if conversation_id is not None:
            # Ownership *and* snapshot, per §23.1 — a conversation belongs to a
            # corpus, and replaying one against a different snapshot would let
            # its stored citations point at lines that have moved.
            convo = await queries.owned_conversation(
                conn, conversation_id, user["id"], snapshot_id
            )
            if convo is None:
                raise ConversationNotFoundError(conversation_id)
            history = await queries.conversation_turns(
                conn, conversation_id, CONVERSATION_CONTEXT_TURNS
            )
        else:
            # First turn: open the conversation now so the id exists before the
            # stream starts, and the client can resume even if it disconnects
            # mid-answer.
            conversation_id = await queries.create_conversation(
                conn, snapshot_id, user["id"], title=body.question[:200]
            )

    if not chat_slots.try_acquire():
        raise ServiceBusyError(
            f"all {chat_slots.limit} answer slots are busy",
            retry_after=30,
            rule="chat_concurrency",
        )
    metrics.chat_streams_active.set(chat_slots.used)

    def release() -> None:
        chat_slots.release()
        metrics.chat_streams_active.set(chat_slots.used)

    return EventSourceResponse(
        chat_event_stream(
            model,
            pool,
            snapshot_id,
            body.question,
            on_finish=release,
            history=history,
            conversation_id=conversation_id,
        )
    )
