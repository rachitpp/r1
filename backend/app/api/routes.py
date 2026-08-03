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
import json
import logging
from typing import Any
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Query, Request, Response, status
from sse_starlette.sse import EventSourceResponse

from app import metrics
from app.agent.citations import validate_citations
from app.api.chat_stream import chat_event_stream
from app.api.deps import Arq, ChatModel, Conn, CurrentUser, Pool
from app.api.ratelimit import Slots
from app.api.schemas import (
    ArchitectureOut,
    ChangedSymbol,
    ChatRequest,
    ChecklistItemOut,
    ChecklistOut,
    CompareCommit,
    CompareOut,
    ConversationDetail,
    ConversationList,
    ConversationOut,
    CoverageOut,
    DependenciesOut,
    DependencyOut,
    DependencyUse,
    DependencyUsesOut,
    FileOut,
    HistoryOut,
    ModuleEdge,
    ModuleNode,
    OverviewOut,
    ReadyCheck,
    ReadyOut,
    RepoCreate,
    RepoList,
    RepoOut,
    ShareCreated,
    SharedAnswerOut,
    ShareRequest,
    SnapshotRef,
    SymbolRef,
    TraceNode,
    TraceOut,
    UnusedDependency,
)
from app.checklist import build_checklist
from app.config import (
    ARCH_MAX_EDGES,
    ARCH_MAX_NODES,
    COMPARE_MAX_COMMITS,
    COMPARE_MAX_ITEMS,
    CONVERSATION_CONTEXT_TURNS,
    CONVERSATION_PAGE_MAX,
    COVERAGE_MAX_LINKS,
    DEPENDENCY_MAX_PACKAGES,
    DEPENDENCY_MAX_USES,
    FILE_RANGE_MAX_LINES,
    HISTORY_PAGE_MAX,
    OVERVIEW_MAX_API_SYMBOLS,
    TRACE_MAX_DEPTH,
    TRACE_MAX_NODES,
    get_settings,
)
from app.db import queries
from app.db.pool import acquire, sample_pool_gauges
from app.exceptions import (
    ConversationNotFoundError,
    InvalidLineRangeError,
    RepoFileNotFoundError,
    RepoNotFoundError,
    RepoNotReadyError,
    ServiceBusyError,
    SharedAnswerNotFoundError,
    SnapshotsNotComparable,
    SymbolNotFoundError,
    UnauthorizedError,
)
from app.ingest.dependencies import distribution_for
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


async def _require_owned_repo(
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
    existing = await queries.newest_snapshot_for_source(conn, source_id)

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
    await arq.enqueue_job("ingest_repo", str(snapshot_id))
    logger.info("enqueued ingest for %s (snapshot %s)", name, snapshot_id)

    row = await _require_owned_repo(conn, user["id"], snapshot_id)
    response.status_code = status.HTTP_201_CREATED
    return RepoOut.from_row(row)


@router.get("/repos", response_model=RepoList)
async def list_repos(conn: Conn, user: CurrentUser) -> RepoList:
    """The caller's library only (§13.6) — never every repo in the database."""
    rows = await queries.list_repos(conn, user["id"])
    return RepoList(repos=[RepoOut.from_row(r) for r in rows])


@router.get("/repos/{snapshot_id}", response_model=RepoOut)
async def get_repo(snapshot_id: UUID, conn: Conn, user: CurrentUser) -> RepoOut:
    return RepoOut.from_row(await _require_owned_repo(conn, user["id"], snapshot_id))


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
    row = await _require_owned_repo(conn, user["id"], snapshot_id)

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


@router.get("/repos/{snapshot_id}/architecture", response_model=ArchitectureOut)
async def get_repo_architecture(
    snapshot_id: UUID,
    conn: Conn,
    user: CurrentUser,
    include_tests: bool = Query(False),
) -> ArchitectureOut:
    """The module dependency rollup (§18.2).

    Two aggregations over the symbol graph that already exists — no model call,
    no tool budget, no ingest work. Deterministic, so the same snapshot always
    answers the same map; snapshots are immutable (§14.3), so a client may cache
    it for as long as it likes.

    Ranked by fan-in and truncated at the §12 caps rather than paginated: this
    is an overview, and page two of a module map is not an overview.
    """
    await _require_owned_repo(conn, user["id"], snapshot_id)
    nodes = await queries.module_nodes(
        conn, snapshot_id, include_tests=include_tests, limit=ARCH_MAX_NODES
    )
    edges = await queries.module_edges(
        conn, snapshot_id, include_tests=include_tests, limit=ARCH_MAX_EDGES
    )
    return ArchitectureOut(
        nodes=[
            ModuleNode(path=p, n_symbols=n, fan_in=fi, fan_out=fo)
            for p, n, fi, fo in nodes
        ],
        edges=[
            ModuleEdge(from_path=f, to_path=t, kind=k, weight=w)
            for f, t, k, w in edges
        ],
        include_tests=include_tests,
        truncated=len(nodes) >= ARCH_MAX_NODES or len(edges) >= ARCH_MAX_EDGES,
    )


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
    await _require_owned_repo(conn, user["id"], snapshot_id)

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


@router.get("/repos/{snapshot_id}/compare", response_model=CompareOut)
async def compare_snapshots(
    snapshot_id: UUID,
    base: UUID,
    conn: Conn,
    user: CurrentUser,
) -> CompareOut:
    """What changed between two snapshots of the same repo (§28.2).

    Reads as "this snapshot, compared against `base`" — the path names what you
    are looking at and the query names what you are looking back to, which is
    the direction a reader arrives from a repo page with.

    A *structural* diff. `git diff` answers "which lines changed" and answers it
    better; what only this can answer is what the **index** now holds — which
    files, symbols and third-party packages exist that did not before. Costs no
    model call: four SQL statements over two immutable corpora.
    """
    await _require_owned_repo(conn, user["id"], snapshot_id)
    await _require_owned_repo(conn, user["id"], base)
    meta = await queries.snapshot_meta(conn, [snapshot_id, base])
    head_row, base_row = meta[snapshot_id], meta[base]

    # Two guards, both because a confident wrong answer is worse than a refusal.
    if head_row["source_id"] != base_row["source_id"]:
        raise SnapshotsNotComparable(
            "those snapshots are of different repositories"
        )
    if head_row["strategy"] != base_row["strategy"]:
        # `naive` stores no symbols at all (§2.7), so this pairing would report
        # every symbol in the repo as deleted — a number that looks like a
        # finding and is an artefact of the question.
        raise SnapshotsNotComparable(
            f"those snapshots were chunked differently "
            f"({base_row['strategy']} vs {head_row['strategy']}); "
            "a comparison across strategies measures the chunker, not the code"
        )

    files_added, files_removed = await queries.compare_files(
        conn, base, snapshot_id, COMPARE_MAX_ITEMS
    )
    sym_added, sym_removed = await queries.compare_symbols(
        conn, base, snapshot_id, COMPARE_MAX_ITEMS
    )
    deps_added, deps_removed = await queries.compare_dependencies(
        conn, base, snapshot_id, COMPARE_MAX_ITEMS
    )
    commits_indexed = await queries.has_history(
        conn, snapshot_id
    ) and await queries.has_history(conn, base)
    commits = (
        await queries.commits_between(conn, base, snapshot_id, COMPARE_MAX_COMMITS)
        if commits_indexed
        else []
    )

    def _ref(row: asyncpg.Record) -> SnapshotRef:
        return SnapshotRef(
            id=row["id"],
            commit_sha=row["commit_sha"],
            strategy=str(row["strategy"]),
            created_at=row["created_at"],
        )

    return CompareOut(
        base=_ref(base_row),
        head=_ref(head_row),
        files_added=files_added,
        files_removed=files_removed,
        symbols_added=[
            ChangedSymbol(
                qualname=str(r["qualname"]),
                kind=str(r["kind"]),
                file_path=str(r["file_path"]),
            )
            for r in sym_added
        ],
        symbols_removed=[
            ChangedSymbol(
                qualname=str(r["qualname"]),
                kind=str(r["kind"]),
                file_path=str(r["file_path"]),
            )
            for r in sym_removed
        ],
        dependencies_added=deps_added,
        dependencies_removed=deps_removed,
        commits_indexed=commits_indexed,
        commits=[
            CompareCommit(
                sha=str(c["sha"]),
                author_name=str(c["author_name"]),
                authored_at=c["authored_at"],
                subject=str(c["subject"]),
            )
            for c in commits
        ],
        truncated=(
            len(files_added) + len(files_removed) >= COMPARE_MAX_ITEMS
            or len(sym_added) + len(sym_removed) >= COMPARE_MAX_ITEMS
        ),
    )


@router.get("/repos/{snapshot_id}/dependencies", response_model=DependenciesOut)
async def get_repo_dependencies(
    snapshot_id: UUID,
    conn: Conn,
    user: CurrentUser,
    include_tests: bool = False,
) -> DependenciesOut:
    """What this repo stands on, and what it declares (§26.2).

    Three lists rather than one, because the disagreements are the useful part:
    ``packages`` is what the code imports, ``undeclared`` is what it imports
    without asking for, and ``unused`` is what it asks for without importing.

    ``include_tests`` applies to the first two only. `unused` always counts a
    test-suite import as usage — reporting `pytest` as an unused dependency
    because tests were filtered out would be a false alarm of the worst kind:
    plausible, and wrong.
    """
    await _require_owned_repo(conn, user["id"], snapshot_id)
    if not await queries.has_dependencies(conn, snapshot_id):
        # §26.3: ingested before the dependency pass existed. Say so rather
        # than let an empty list read as "this project has no dependencies".
        return DependenciesOut(
            indexed=False,
            include_tests=include_tests,
            packages=[],
            undeclared=[],
            unused=[],
            truncated=False,
        )

    rows = await queries.dependency_summary(
        conn, snapshot_id, include_tests=include_tests, limit=DEPENDENCY_MAX_PACKAGES
    )
    undeclared = await queries.undeclared_dependencies(
        conn, snapshot_id, include_tests=include_tests, limit=DEPENDENCY_MAX_PACKAGES
    )
    unused = await queries.unused_dependencies(
        conn, snapshot_id, limit=DEPENDENCY_MAX_PACKAGES
    )

    # Alias reconciliation (§26.2). SQL matched on the name as written, which
    # reports `python-dotenv` as unused *and* `dotenv` as undeclared — one
    # package, two contradictory findings. Resolving it here keeps the lookup
    # table in one language.
    declared = await queries.declared_by_name(conn, snapshot_id)
    used_distributions = {
        distribution_for(str(r["module"])) for r in rows
    } | {distribution_for(str(r["module"])) for r in undeclared}

    packages = []
    for row in rows:
        package = DependencyOut.from_row(row)
        if not package.declared:
            match = declared.get(distribution_for(package.module))
            if match is not None:
                # Carry the manifest across too: "declared" beside an empty
                # requirement reads as a bug in the panel.
                package.declared = True
                package.requirement = str(match["requirement"])
                package.sources = list(match["sources"] or [])
                package.extras = list(match["extras"] or [])
        packages.append(package)

    return DependenciesOut(
        indexed=True,
        include_tests=include_tests,
        packages=packages,
        undeclared=[
            str(r["module"])
            for r in undeclared
            if distribution_for(str(r["module"])) not in declared
        ],
        unused=[
            UnusedDependency(
                name=str(r["name"]),
                requirement=str(r["requirement"]),
                sources=list(r["sources"] or []),
                extras=list(r["extras"] or []),
            )
            for r in unused
            if str(r["name"]) not in used_distributions
        ],
        truncated=len(rows) >= DEPENDENCY_MAX_PACKAGES,
    )


@router.get(
    "/repos/{snapshot_id}/dependencies/{module}", response_model=DependencyUsesOut
)
async def get_repo_dependency_uses(
    snapshot_id: UUID,
    module: str,
    conn: Conn,
    user: CurrentUser,
    include_tests: bool = False,
) -> DependencyUsesOut:
    """Every import site for one package (§26.2).

    An unknown module returns an empty list rather than a 404, for the §18.3
    reason one level down: "where is X used" and "X is not used" are the same
    answer, and a 404 would turn this into an existence oracle for package
    names in someone else's repo.
    """
    await _require_owned_repo(conn, user["id"], snapshot_id)
    rows = await queries.dependency_uses(
        conn,
        snapshot_id,
        module,
        include_tests=include_tests,
        limit=DEPENDENCY_MAX_USES,
    )
    return DependencyUsesOut(
        module=module,
        include_tests=include_tests,
        uses=[
            DependencyUse(
                dotted=str(r["dotted"]),
                file_path=str(r["file_path"]),
                start_line=int(r["start_line"]),
                is_test=bool(r["is_test"]),
            )
            for r in rows
        ],
        truncated=len(rows) >= DEPENDENCY_MAX_USES,
    )


@router.get("/repos/{snapshot_id}/coverage", response_model=CoverageOut)
async def get_repo_coverage(
    snapshot_id: UUID,
    path: str,
    conn: Conn,
    user: CurrentUser,
) -> CoverageOut:
    """Test ↔ implementation links for one file (§18.3).

    An unknown ``path`` returns empty lists, not a 404: a file with no symbols
    and a file that is not in the index are the same answer to "what tests reach
    this?", and distinguishing them would make the endpoint an existence oracle
    for paths in someone else's repo — the §13.5 reasoning, one level down.
    """
    await _require_owned_repo(conn, user["id"], snapshot_id)
    covered = await queries.tests_covering_file(
        conn, snapshot_id, path, COVERAGE_MAX_LINKS
    )
    covers = await queries.implementation_covered_by_file(
        conn, snapshot_id, path, COVERAGE_MAX_LINKS
    )
    return CoverageOut.from_rows(
        path,
        covered,
        covers,
        truncated=len(covered) >= COVERAGE_MAX_LINKS
        or len(covers) >= COVERAGE_MAX_LINKS,
    )


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
    await _require_owned_repo(conn, user["id"], snapshot_id)
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


@router.get(
    "/repos/{snapshot_id}/conversations", response_model=ConversationList
)
async def list_repo_conversations(
    snapshot_id: UUID, conn: Conn, user: CurrentUser
) -> ConversationList:
    """This caller's conversations about this repo, most recently used first (§23.4)."""
    await _require_owned_repo(conn, user["id"], snapshot_id)
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
    await _require_owned_repo(conn, user["id"], snapshot_id)
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
    await _require_owned_repo(conn, user["id"], snapshot_id)
    if not await queries.delete_conversation(conn, conversation_id, user["id"]):
        raise ConversationNotFoundError(conversation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/repos/{snapshot_id}/trace", response_model=TraceOut)
async def get_repo_trace(
    snapshot_id: UUID,
    symbol: str,
    conn: Conn,
    user: CurrentUser,
    direction: str = Query("out", pattern="^(in|out)$"),
    depth: int = Query(TRACE_MAX_DEPTH, ge=1, le=TRACE_MAX_DEPTH),
    include_tests: bool = Query(False),
) -> TraceOut:
    """Transitive call hierarchy from one symbol (§24.2).

    `expand_context` and `find_references` each do **one hop** and return code;
    this walks several and returns a **path**. The difference matters: "what
    does this reach, eventually" is a shape question, and answering it by
    pulling every reached body into a model context would spend the §12 token
    budget to say something a list of pointers says better.

    An endpoint rather than a seventh tool, per §18.1 — a bounded graph walk is
    exact SQL, so routing it through the model would spend from the budget of 8
    to compute what a recursive CTE already knows.

    `direction=out` is what this symbol reaches; `in` is what reaches it.
    """
    await _require_owned_repo(conn, user["id"], snapshot_id)
    root = await queries.find_symbol(
        conn, snapshot_id, symbol, include_tests=include_tests
    )
    if root is None:
        raise SymbolNotFoundError(symbol)
    # A class is traced through its methods — see `symbol_seed_ids`.
    seeds = await queries.symbol_seed_ids(conn, snapshot_id, root)
    nodes = await queries.trace_graph(
        conn,
        snapshot_id,
        seeds,
        direction=direction,
        max_depth=depth,
        limit=TRACE_MAX_NODES,
        include_tests=include_tests,
    )
    return TraceOut(
        root=SymbolRef(
            qualname=root["qualname"],
            file_path=root["file_path"],
            line=root["start_line"],
        ),
        direction=direction,
        max_depth=depth,
        nodes=[TraceNode(**dict(n)) for n in nodes],
        truncated=len(nodes) >= TRACE_MAX_NODES,
    )


@router.get("/repos/{snapshot_id}/checklist", response_model=ChecklistOut)
async def get_repo_checklist(
    snapshot_id: UUID,
    conn: Conn,
    user: CurrentUser,
) -> ChecklistOut:
    """"The first five things to understand about this repo" (§22.2).

    **No model call**, which is the whole design. FEATURE-IDEAS pairs 6.5 with
    3.1 and the obvious reading is "a second generated document" — a second
    request per snapshot against a tier that allows twenty a day. §18.1 already
    settled it: if the symbol graph can answer it exactly, it is a query. Which
    module everything imports, where execution starts, what the package
    exports, what the tests hit hardest — all `GROUP BY`s §19 was already
    running. A model would contribute phrasing, and phrasing is a template.

    Deterministic over an immutable snapshot (§14.3), so a client may cache it
    for as long as it holds the id.
    """
    await _require_owned_repo(conn, user["id"], snapshot_id)
    file_rows = await conn.fetch(
        "SELECT path, n_lines FROM files WHERE snapshot_id = $1", snapshot_id
    )
    items = build_checklist(
        entry_points=await queries.entry_point_candidates(conn, snapshot_id, 1),
        # `module_nodes` hands back positional tuples, not records — adapt
        # here rather than teaching the builder two row shapes.
        modules=[
            {"path": p, "n_symbols": n, "fan_in": fi, "fan_out": fo}
            for p, n, fi, fo in await queries.module_nodes(
                conn, snapshot_id, include_tests=False, limit=1
            )
        ],
        key_symbols=await queries.most_referenced_symbols(conn, snapshot_id, 1),
        # More candidates than the step shows: `_package_surface` filters to
        # this package and dedupes, and needs something to filter.
        api_symbols=await queries.public_api_symbols(
            conn, snapshot_id, OVERVIEW_MAX_API_SYMBOLS
        ),
        tested_files=await queries.most_tested_files(conn, snapshot_id, 1),
        n_lines_of={str(r["path"]): int(r["n_lines"]) for r in file_rows},
    )
    return ChecklistOut(
        items=[ChecklistItemOut(**vars(i)) for i in items],
    )


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
    await _require_owned_repo(conn, user["id"], snapshot_id)
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
        row = await _require_owned_repo(conn, user["id"], snapshot_id)
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
