"""HTTP routes (SPEC §8). Thin: parse, delegate, shape the response.

No route clones, parses, or embeds anything (CLAUDE.md hard rule 1) — ``POST
/repos`` enqueues an ARQ job and returns. Failures are raised as typed
exceptions from :mod:`app.exceptions` and mapped to status codes by the handlers
in :mod:`app.api.errors`; no route builds an ``HTTPException`` itself.
"""

from __future__ import annotations

import logging
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Response, status
from sse_starlette.sse import EventSourceResponse

from app.api.chat_stream import chat_event_stream
from app.api.deps import Arq, ChatModel, Conn
from app.api.schemas import (
    ChatRequest,
    FileOut,
    RepoCreate,
    RepoList,
    RepoOut,
)
from app.db import queries
from app.exceptions import (
    RepoFileNotFoundError,
    RepoNotFoundError,
    RepoNotReadyError,
)
from app.ingest.urls import normalize_github_url

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, bool]:
    return {"ok": True}


async def _require_repo(conn: asyncpg.Connection, repo_id: UUID) -> asyncpg.Record:
    row = await queries.get_repo(conn, repo_id)
    if row is None:
        raise RepoNotFoundError(repo_id)
    return row


@router.post("/repos", response_model=RepoOut)
async def create_repo(
    body: RepoCreate, response: Response, conn: Conn, arq: Arq
) -> RepoOut:
    """Register a repo and queue its ingest (§8: 201 created / 200 already known).

    A repeat submission of a known URL is a re-ingest request, not an error — but
    only when nothing is already working on it, or a double-click would put two
    workers through the same delete-and-replace.
    """
    url, name = normalize_github_url(body.url)
    repo_id, created = await queries.create_repo(conn, url=url, name=name)
    row = await _require_repo(conn, repo_id)

    if created or row["status"] not in queries.IN_FLIGHT_STATUSES:
        if not created:
            await queries.start_ingest(conn, repo_id, status="queued")
            row = await _require_repo(conn, repo_id)
        await arq.enqueue_job("ingest_repo", str(repo_id))
        logger.info("enqueued ingest for %s (%s)", name, repo_id)

    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return RepoOut.from_row(row)


@router.get("/repos", response_model=RepoList)
async def list_repos(conn: Conn) -> RepoList:
    rows = await queries.list_repos(conn)
    return RepoList(repos=[RepoOut.from_row(r) for r in rows])


@router.get("/repos/{repo_id}", response_model=RepoOut)
async def get_repo(repo_id: UUID, conn: Conn) -> RepoOut:
    return RepoOut.from_row(await _require_repo(conn, repo_id))


@router.get("/repos/{repo_id}/files", response_model=FileOut)
async def get_repo_file(repo_id: UUID, path: str, conn: Conn) -> FileOut:
    """Serve a stored file for the viewer and citation clicks (§8).

    Content comes from the ``files`` table, never from disk: the clone is deleted
    when ingestion finishes (§2.1), and the database is the durable copy.
    """
    await _require_repo(conn, repo_id)
    row = await queries.get_file(conn, repo_id, path)
    if row is None:
        raise RepoFileNotFoundError(path)
    return FileOut(path=row["path"], content=row["content"], n_lines=row["n_lines"])


@router.post("/repos/{repo_id}/chat")
async def chat(
    repo_id: UUID, body: ChatRequest, conn: Conn, model: ChatModel
) -> EventSourceResponse:
    """Stream an agent answer as §9 SSE events (SSE only — hard rule 7)."""
    row = await _require_repo(conn, repo_id)
    if row["status"] != "ready":
        raise RepoNotReadyError(str(row["status"]))
    return EventSourceResponse(
        chat_event_stream(model, conn, repo_id, body.question)
    )
