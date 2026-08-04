"""Repo submission and reads (SPEC §8, §10).

``POST /repos`` enqueues an ARQ job and returns — no route clones,
parses, or embeds anything (CLAUDE.md hard rule 1)."""

from __future__ import annotations

import hashlib
import logging
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response, status

from app.api.deps import Arq, Conn, CurrentUser
from app.api.routes._common import CACHE_IMMUTABLE, require_owned_repo
from app.api.schemas import (
    FileOut,
    RepoCreate,
    RepoList,
    RepoOut,
)
from app.config import (
    FILE_RANGE_MAX_LINES,
    get_settings,
)
from app.db import queries
from app.exceptions import (
    InvalidLineRangeError,
    RepoFileNotFoundError,
    ServiceBusyError,
)
from app.ingest.urls import normalize_github_url

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/repos", response_model=RepoOut)
async def create_repo(
    body: RepoCreate, response: Response, conn: Conn, arq: Arq, user: CurrentUser
) -> RepoOut:
    """Register a repo and queue its ingest (§8: 201 created / 200 already known).

    §14.5 changed what a repeat submission means. A URL whose newest snapshot is
    ``ready`` now **returns that snapshot** rather than re-ingesting it: the old
    behaviour cleared the corpus in place, which is the race this phase exists
    to remove. A ``failed`` snapshot is superseded by a brand-new one — never
    reset — so the frontend's Retry button still works. An in-flight snapshot is
    joined.

    Either way the caller gets a library link, so a submission never leaves a
    repo the submitter cannot see. A second user submitting a known URL joins
    the existing corpus instead of duplicating it, which is where the
    ingest-volume saving starts; the worker's post-clone check (§14.4) is where
    it finishes.

    Refuses past ``MAX_ACTIVE_INGESTS``. The queue would otherwise accept work
    indefinitely: ARQ is happy to hold ten thousand jobs, and the machine that
    has to run them is the same one serving chat.
    """
    url, name = normalize_github_url(body.url)
    source_id = await queries.get_or_create_source(conn, url=url, name=name)
    # A pinned rev skips the "return the newest snapshot" shortcut: the caller
    # is asking for a *particular* commit, and the newest snapshot is by
    # definition not it. Whether that commit is already indexed cannot be known
    # here — a rev may be a tag or a short sha — so the worker's post-clone
    # §14.4 check decides, and returns the existing corpus if there is one.
    existing = (
        None
        if body.rev
        else await queries.newest_snapshot_for_source(conn, source_id)
    )

    # §14.5. A `ready` snapshot is returned as it is — re-submitting a URL is no
    # longer a destructive re-ingest, which is the whole point of the phase: the
    # corpus somebody is reading is never the corpus somebody else is
    # rebuilding. An in-flight snapshot is joined rather than duplicated.
    if existing is not None and existing["status"] != "failed":
        await queries.link_user_repo(conn, user["id"], existing["id"])
        response.status_code = status.HTTP_200_OK
        return RepoOut.from_row(existing)

    # Nothing usable: either no snapshot at all, or the newest one failed. A
    # retry is a *new* snapshot, never a reset of the failed row (§14.3).
    # §15.5: per *user*, not global. As a global count, one person's queued
    # repos refused everybody else's first submission — a per-user limit
    # masquerading as capacity protection. Real capacity is bounded by the size
    # of the worker fleet and by the §15.3 one-in-flight-per-source index.
    limit = get_settings().MAX_ACTIVE_INGESTS
    active = await queries.count_active_ingests_for_user(conn, user["id"])
    if active >= limit:
        raise ServiceBusyError(
            f"you already have {active} ingests queued or running (limit {limit})",
            retry_after=60,
            rule="ingest_capacity",
        )

    snapshot_id = await queries.create_snapshot(conn, source_id)
    await queries.link_user_repo(conn, user["id"], snapshot_id)
    await arq.enqueue_job("ingest_repo", str(snapshot_id), body.rev)
    logger.info(
        "enqueued ingest for %s (snapshot %s, rev %s)",
        name,
        snapshot_id,
        body.rev or "HEAD",
    )

    row = await require_owned_repo(conn, user["id"], snapshot_id)
    response.status_code = status.HTTP_201_CREATED
    return RepoOut.from_row(row)


@router.get("/repos", response_model=RepoList)
async def list_repos(conn: Conn, user: CurrentUser) -> RepoList:
    """The caller's library only (§13.6) — never every repo in the database."""
    rows = await queries.list_repos(conn, user["id"])
    return RepoList(repos=[RepoOut.from_row(r) for r in rows])


@router.get("/repos/{snapshot_id}", response_model=RepoOut)
async def get_repo(snapshot_id: UUID, conn: Conn, user: CurrentUser) -> RepoOut:
    return RepoOut.from_row(await require_owned_repo(conn, user["id"], snapshot_id))


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


@router.get("/repos/{snapshot_id}/files", response_model=FileOut)
async def get_repo_file(
    snapshot_id: UUID,
    path: str,
    request: Request,
    response: Response,
    conn: Conn,
    user: CurrentUser,
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
    row = await require_owned_repo(conn, user["id"], snapshot_id)

    etag = _file_etag(row["head_sha"], path, start_line, end_line)
    if etag and _matches_etag(request.headers.get("if-none-match"), etag):
        return Response(
            status_code=status.HTTP_304_NOT_MODIFIED,
            headers={"ETag": etag, "Cache-Control": CACHE_IMMUTABLE},
        )

    file_row = await queries.get_file(conn, snapshot_id, path)
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
