"""HTTP routes (SPEC §8). Thin: parse, delegate, shape the response.

No route clones, parses, or embeds anything (CLAUDE.md hard rule 1) — ``POST
/repos`` enqueues an ARQ job and returns. Failures are raised as typed
exceptions from :mod:`app.exceptions` and mapped to status codes by the handlers
in :mod:`app.api.errors`; no route builds an ``HTTPException`` itself.

Three operational endpoints sit alongside the §8 API and are documented where
they are defined: ``/health`` (liveness), ``/ready`` (can this process actually
serve anything), and ``/metrics``.
"""

from __future__ import annotations

import hashlib
import logging
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Query, Request, Response, status
from sse_starlette.sse import EventSourceResponse

from app import metrics
from app.api.chat_stream import chat_event_stream
from app.api.deps import Arq, ChatModel, Conn, Pool
from app.api.ratelimit import Slots
from app.api.schemas import (
    ChatRequest,
    FileOut,
    ReadyCheck,
    ReadyOut,
    RepoCreate,
    RepoList,
    RepoOut,
)
from app.config import FILE_RANGE_MAX_LINES, get_settings
from app.db import queries
from app.db.pool import acquire, sample_pool_gauges
from app.exceptions import (
    InvalidLineRangeError,
    RepoFileNotFoundError,
    RepoNotFoundError,
    RepoNotReadyError,
    ServiceBusyError,
    UnauthorizedError,
)
from app.ingest.urls import normalize_github_url

logger = logging.getLogger(__name__)

router = APIRouter()

# Concurrent agent runs this process will accept. Module-level because it is a
# property of the process, not of a request: it guards the cores, the inference
# threads, and the connection pool that every stream shares.
chat_slots = Slots(get_settings().CHAT_MAX_CONCURRENCY)

# File contents are immutable for a given commit, so the browser may keep them
# for as long as it likes — the ETag names the commit, and a new commit is a new
# ETag rather than a stale one.
CACHE_IMMUTABLE = "private, max-age=31536000, immutable"


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------


@router.get("/health")
async def health() -> dict[str, bool]:
    """Liveness. Deliberately trivial: is this process running at all.

    It must not touch Postgres or Redis. A liveness probe that fails when a
    dependency is down gets the process *restarted* for someone else's outage,
    which turns a degraded API into no API. "Can it serve?" is ``/ready``.
    """
    return {"ok": True}


@router.get("/ready", response_model=ReadyOut)
async def ready(request: Request, response: Response) -> ReadyOut:
    """Readiness: can this process serve a real request right now.

    Startup tolerates an unreachable Postgres or Redis on purpose (see
    :mod:`app.main`), which is what makes this endpoint necessary rather than
    decorative: without it, a process that will 503 every single request still
    reports itself healthy, and a load balancer routes traffic straight into it.

    Postgres is required. Redis is required too — without it ``POST /repos``
    cannot enqueue, and a node that can only answer reads is not ready. The
    embedder is reported but *not* required: it loads lazily on first use, so a
    cold model is a slow first search, not a broken node.
    """
    checks: dict[str, ReadyCheck] = {}

    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        checks["postgres"] = ReadyCheck(ok=False, detail="no pool")
    else:
        try:
            async with acquire(pool) as conn:
                await conn.fetchval("SELECT 1")
            checks["postgres"] = ReadyCheck(ok=True)
        except Exception as exc:  # noqa: BLE001 — the check *is* the error path
            checks["postgres"] = ReadyCheck(ok=False, detail=type(exc).__name__)

    arq = getattr(request.app.state, "arq", None)
    if arq is None:
        checks["redis"] = ReadyCheck(ok=False, detail="not connected")
    else:
        try:
            await arq.ping()
            checks["redis"] = ReadyCheck(ok=True)
        except Exception as exc:  # noqa: BLE001
            checks["redis"] = ReadyCheck(ok=False, detail=type(exc).__name__)

    warm = bool(getattr(request.app.state, "embedder_ready", False))
    checks["embedder"] = ReadyCheck(
        ok=True, detail="warm" if warm else "cold (loads on first use)"
    )

    ok = checks["postgres"].ok and checks["redis"].ok
    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadyOut(ok=ok, checks=checks)


@router.get("/metrics")
async def metrics_endpoint(request: Request) -> Response:
    """Prometheus text exposition (see :mod:`app.metrics`).

    Guarded by ``METRICS_TOKEN`` when one is set. Metrics are not secret in the
    way credentials are, but they do enumerate repo counts, error rates, and
    what this box is doing — restrict the network or set the token.
    """
    settings = get_settings()
    if not settings.METRICS_ENABLED:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    if settings.METRICS_TOKEN:
        header = request.headers.get("authorization", "")
        if header != f"Bearer {settings.METRICS_TOKEN}":
            raise UnauthorizedError("metrics require a bearer token")

    sample_pool_gauges(getattr(request.app.state, "pool", None))
    metrics.chat_streams_active.set(chat_slots.used)
    return Response(
        content=metrics.render(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


# ---------------------------------------------------------------------------
# §8 API
# ---------------------------------------------------------------------------


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

    Refuses past ``MAX_ACTIVE_INGESTS``. The queue would otherwise accept work
    indefinitely: ARQ is happy to hold ten thousand jobs, and the machine that
    has to run them is the same one serving chat.
    """
    url, name = normalize_github_url(body.url)
    repo_id, created = await queries.create_repo(conn, url=url, name=name)
    row = await _require_repo(conn, repo_id)

    if created or row["status"] not in queries.IN_FLIGHT_STATUSES:
        limit = get_settings().MAX_ACTIVE_INGESTS
        active = await queries.count_active_ingests(conn)
        # A re-ingest of a row that is already counted must not be blocked by
        # its own presence in that count.
        if active - (1 if row["status"] in queries.ACTIVE_STATUSES else 0) >= limit:
            raise ServiceBusyError(
                f"{active} ingests already queued or running (limit {limit})",
                retry_after=60,
                rule="ingest_capacity",
            )
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


def _file_etag(
    head_sha: str | None, path: str, start: int | None, end: int | None
) -> str | None:
    """A strong ETag for this file at this commit, or ``None`` if unknowable.

    Keyed on the commit rather than on the content, so a repeat request can be
    answered 304 *without reading the file out of Postgres* — the saving is a
    row fetch as well as the bytes on the wire.

    ``None`` when the repo has no ``head_sha`` yet (it has never finished
    cloning). There is nothing stable to name then, and a wrong ETag is far
    worse than no ETag: it caches the wrong bytes forever.
    """
    if not head_sha:
        return None
    key = f"{head_sha}:{path}:{start}-{end}".encode()
    return f'"{hashlib.sha256(key).hexdigest()[:32]}"'


def _matches_etag(header: str | None, etag: str) -> bool:
    """RFC 9110 ``If-None-Match``: a ``*`` or a list containing this tag."""
    if not header:
        return False
    candidates = {value.strip() for value in header.split(",")}
    if "*" in candidates:
        return True
    # A cache may weaken a strong tag on revalidation; compare on the opaque
    # part so `W/"abc"` still matches `"abc"`.
    return any(c.removeprefix("W/") == etag for c in candidates)


def _slice_lines(
    content: str, n_lines: int, start: int | None, end: int | None
) -> tuple[str, int, int]:
    """Return ``(text, first_line, last_line)`` for the requested window.

    No range means the whole file, unchanged — including its exact trailing
    newline, which is why this splits with ``keepends`` instead of rejoining
    with ``"\\n"``.
    """
    if start is None and end is None:
        return content, 1, n_lines
    first = start or 1
    # A start with no end is "a screenful from here", not "the rest of a
    # 40_000-line file" — an unbounded range is the same response as no range.
    last = end if end is not None else first + FILE_RANGE_MAX_LINES - 1
    if last < first:
        raise InvalidLineRangeError(first, last)
    if first > n_lines:
        # Past the end of the file: an empty window, reported as one. `end <
        # start` is the same shape an empty file already returns (1, 0), rather
        # than a second convention for the same fact.
        return "", first, first - 1
    last = min(last, first + FILE_RANGE_MAX_LINES - 1, n_lines)
    lines = content.splitlines(keepends=True)
    return "".join(lines[first - 1 : last]), first, last


@router.get("/repos/{repo_id}/files", response_model=FileOut)
async def get_repo_file(
    repo_id: UUID,
    path: str,
    request: Request,
    response: Response,
    conn: Conn,
    start_line: int | None = Query(None, ge=1),
    end_line: int | None = Query(None, ge=1),
) -> FileOut | Response:
    """Serve a stored file for the viewer and citation clicks (§8).

    Content comes from the ``files`` table, never from disk: the clone is deleted
    when ingestion finishes (§2.1), and the database is the durable copy.

    Cacheable, because it is the same bytes every time. Every citation click
    refetches a file that cannot have changed — the commit is pinned — so
    without an ETag a session re-downloads the same few hundred kilobytes over
    and over. ``start_line``/``end_line`` bound the response for the common case
    where the viewer only renders a window.
    """
    row = await _require_repo(conn, repo_id)

    etag = _file_etag(row["head_sha"], path, start_line, end_line)
    if etag and _matches_etag(request.headers.get("if-none-match"), etag):
        return Response(
            status_code=status.HTTP_304_NOT_MODIFIED,
            headers={"ETag": etag, "Cache-Control": CACHE_IMMUTABLE},
        )

    file_row = await queries.get_file(conn, repo_id, path)
    if file_row is None:
        raise RepoFileNotFoundError(path)

    content, first, last = _slice_lines(
        file_row["content"], file_row["n_lines"], start_line, end_line
    )
    if etag:
        response.headers["ETag"] = etag
        response.headers["Cache-Control"] = CACHE_IMMUTABLE
    else:
        # Mid-ingest: there is no commit to key on, so do not let anything cache
        # a file that is about to be replaced.
        response.headers["Cache-Control"] = "no-store"
    return FileOut(
        path=file_row["path"],
        content=content,
        n_lines=file_row["n_lines"],
        start_line=first,
        end_line=last,
    )


@router.post("/repos/{repo_id}/chat")
async def chat(
    repo_id: UUID, body: ChatRequest, pool: Pool, model: ChatModel
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
    async with acquire(pool) as conn:
        row = await _require_repo(conn, repo_id)
    if row["status"] != "ready":
        raise RepoNotReadyError(str(row["status"]))

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
        chat_event_stream(model, pool, repo_id, body.question, on_finish=release)
    )
