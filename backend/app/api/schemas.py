"""Pydantic v2 request/response models (SPEC §8).

Shapes here are the frontend's contract; keep them exactly as §8 specifies. The
SSE event payloads are *not* here — they are transport-level dicts built in
:mod:`app.api.chat_stream` against §9.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Sequence
from typing import Any
from uuid import UUID

import asyncpg
from pydantic import BaseModel, Field

from app.config import (
    QUESTION_MAX_CHARS,
    REPO_URL_MAX_CHARS,
    SHARED_ANSWER_MAX_CHARS,
    SHARED_CITATIONS_MAX,
)


class RepoCreate(BaseModel):
    """``POST /repos`` body. Validation of the URL itself is §8's 422 path."""

    url: str = Field(min_length=1, max_length=REPO_URL_MAX_CHARS)


class RepoProgress(BaseModel):
    files_total: int
    files_parsed: int
    chunks_total: int
    chunks_embedded: int


class RepoOut(BaseModel):
    id: UUID
    url: str
    name: str
    status: str
    error: str | None
    head_sha: str | None
    progress: RepoProgress
    created_at: dt.datetime

    @classmethod
    def from_row(cls, row: asyncpg.Record) -> RepoOut:
        """Build from a ``queries.REPO_COLUMNS`` row."""
        return cls(
            id=row["id"],
            url=row["url"],
            name=row["name"],
            status=row["status"],
            error=row["error"],
            head_sha=row["head_sha"],
            progress=RepoProgress(
                files_total=row["files_total"],
                files_parsed=row["files_parsed"],
                chunks_total=row["chunks_total"],
                chunks_embedded=row["chunks_embedded"],
            ),
            created_at=row["created_at"],
        )


class RepoList(BaseModel):
    repos: list[RepoOut]


class UserOut(BaseModel):
    """``GET /auth/me`` (SPEC §13.3).

    Deliberately narrow: the internal ``id`` and the profile fields the header
    renders. ``github_id`` is not exposed — it is the join key, and nothing in
    the browser has a use for it.
    """

    id: UUID
    login: str
    name: str | None
    avatar_url: str | None
    created_at: dt.datetime

    @classmethod
    def from_row(cls, row: asyncpg.Record) -> UserOut:
        """Build from a ``queries.USER_COLUMNS`` row."""
        return cls(
            id=row["id"],
            login=row["login"],
            name=row["name"],
            avatar_url=row["avatar_url"],
            created_at=row["created_at"],
        )


class FileOut(BaseModel):
    """``GET /repos/{id}/files``.

    ``n_lines`` is the whole file's line count even when a range was requested,
    so a viewer can show "lines 40-80 of 900" without a second call.
    ``start_line``/``end_line`` describe what ``content`` actually contains.
    """

    path: str
    content: str
    n_lines: int
    start_line: int
    end_line: int


class ReadyCheck(BaseModel):
    """One dependency's verdict in the readiness response."""

    ok: bool
    detail: str | None = None


class ReadyOut(BaseModel):
    """``GET /ready``: whether this process can actually serve a request.

    Distinct from ``/health``, which only says the process is alive. Startup
    tolerates an unreachable Postgres or Redis on purpose (see
    :mod:`app.main`), which means "the process is up" and "the process can do
    anything" are genuinely different questions — and routing traffic on the
    first one sends it to a process that will 503 every request.
    """

    ok: bool
    checks: dict[str, ReadyCheck]


class ModuleNode(BaseModel):
    """One module in the §18 rollup. In Python the file *is* the module."""

    path: str
    n_symbols: int
    fan_in: int
    fan_out: int


class ModuleEdge(BaseModel):
    """A directed module→module dependency, weighted by symbol-level edges."""

    from_path: str
    to_path: str
    kind: str
    weight: int


class ArchitectureOut(BaseModel):
    """``GET /repos/{id}/architecture`` (§18.2).

    ``truncated`` is set when either list hit its cap, so a renderer can say
    "top 200 modules" rather than presenting a clipped graph as the whole one.
    """

    nodes: list[ModuleNode]
    edges: list[ModuleEdge]
    include_tests: bool
    truncated: bool


class SymbolRef(BaseModel):
    """A pointer at one symbol: enough to cite it, no code body."""

    qualname: str
    file_path: str
    line: int


class CoveredSymbol(BaseModel):
    """One symbol defined in the requested file, plus the tests that reach it."""

    name: str
    qualname: str
    kind: str
    start_line: int
    end_line: int
    tests: list[SymbolRef]


class CoverageOut(BaseModel):
    """``GET /repos/{id}/coverage`` (§18.3), both directions.

    ``covered`` answers "which tests exercise this file"; ``covers`` answers the
    reverse for a test file and is empty for an implementation file — which is
    the true answer, not a missing case.
    """

    path: str
    covered: list[CoveredSymbol]
    covers: list[SymbolRef]
    truncated: bool

    @classmethod
    def from_rows(
        cls,
        path: str,
        covered_rows: Sequence[asyncpg.Record],
        covers_rows: Sequence[asyncpg.Record],
        *,
        truncated: bool,
    ) -> CoverageOut:
        """Group the flat ``(symbol, test)`` join into one entry per symbol.

        The query orders by ``impl.start_line``, so equal symbols are already
        adjacent and grouping is a single pass that preserves that order.
        """
        covered: list[CoveredSymbol] = []
        by_qualname: dict[tuple[str, int], CoveredSymbol] = {}
        for row in covered_rows:
            key = (str(row["qualname"]), int(row["start_line"]))
            entry = by_qualname.get(key)
            if entry is None:
                entry = CoveredSymbol(
                    name=row["name"],
                    qualname=row["qualname"],
                    kind=row["kind"],
                    start_line=row["start_line"],
                    end_line=row["end_line"],
                    tests=[],
                )
                by_qualname[key] = entry
                covered.append(entry)
            entry.tests.append(
                SymbolRef(
                    qualname=row["ref_qualname"],
                    file_path=row["ref_file_path"],
                    line=row["ref_line"],
                )
            )
        return cls(
            path=path,
            covered=covered,
            covers=[
                SymbolRef(
                    qualname=r["ref_qualname"],
                    file_path=r["ref_file_path"],
                    line=r["ref_line"],
                )
                for r in covers_rows
            ],
            truncated=truncated,
        )


class Citation(BaseModel):
    """A validated `[path:start-end]` reference (§7.5).

    Same shape the §9 `citations` event carries. It gets a model here because
    the overview stores citations rather than streaming them, and a stored
    contract deserves to be typed.
    """

    file_path: str
    start_line: int
    end_line: int


class OverviewOut(BaseModel):
    """``GET /repos/{id}/overview`` (SPEC §19.4).

    ``status`` is the whole contract: ``generating`` means come back (the
    response is 202 and carries no body yet), ``ready`` means the markdown is
    here for good, ``failed`` means the error is worth showing and a retry is
    one more request away.

    ``model`` is exposed because the overview is model-written prose, and a
    reader comparing two repos deserves to know whether the same writer produced
    both.
    """

    status: str
    body: str | None = None
    citations: list[Citation] = []
    model: str | None = None
    error: str | None = None

    @classmethod
    def from_row(cls, row: asyncpg.Record) -> OverviewOut:
        raw = row["citations"]
        # asyncpg hands back JSONB as a string unless a codec is registered;
        # accept both so this does not depend on pool configuration.
        items = json.loads(raw) if isinstance(raw, str) else (raw or [])
        return cls(
            status=row["status"],
            body=row["body"],
            citations=[Citation(**c) for c in items],
            model=row["model"],
            error=row["error"],
        )


class ChatRequest(BaseModel):
    """``POST /repos/{id}/chat`` body.

    Chat is POST, not EventSource: questions do not belong in URLs, and the
    frontend consumes the stream with fetch + ReadableStream (§8).

    The length cap is not decoration: this string goes straight into a model
    context billed per token, so an unbounded field here is an unbounded bill.
    """

    question: str = Field(min_length=1, max_length=QUESTION_MAX_CHARS)
    # §23. Absent means a one-shot run, exactly as before this existed; the
    # frontend sends it only once a conversation has been started.
    conversation_id: UUID | None = None


class CommitOut(BaseModel):
    """One commit in a §20.2 history response.

    ``insertions``/``deletions`` are scoped to the requested path when the
    query was, and commit-wide totals when it was not — see `commit_history`.
    """

    sha: str
    author_name: str
    author_email: str | None
    authored_at: dt.datetime
    subject: str
    body: str | None
    is_merge: bool
    insertions: int
    deletions: int


class HistoryOut(BaseModel):
    """``GET /repos/{id}/history`` (§20.2).

    ``indexed`` is the field that keeps an empty ``commits`` list honest. A
    snapshot ingested before §20 has no rows and neither does a repo with one
    commit; without this flag both read as "nothing ever happened here". Same
    reasoning as §18.3's empty-not-404, one level up: the response distinguishes
    *we did not look* from *there is nothing to see*.
    """

    path: str | None
    indexed: bool
    include_merges: bool
    commits: list[CommitOut]
    truncated: bool

    @classmethod
    def from_rows(
        cls,
        rows: Sequence[Any],
        *,
        path: str | None,
        indexed: bool,
        include_merges: bool,
        limit: int,
    ) -> HistoryOut:
        return cls(
            path=path,
            indexed=indexed,
            include_merges=include_merges,
            commits=[
                CommitOut(
                    sha=r["sha"],
                    author_name=r["author_name"],
                    author_email=r["author_email"],
                    authored_at=r["authored_at"],
                    subject=r["subject"],
                    body=r["body"],
                    is_merge=r["is_merge"],
                    insertions=r["insertions"],
                    deletions=r["deletions"],
                )
                for r in rows
            ],
            truncated=len(rows) >= limit,
        )


class CitationIn(BaseModel):
    """A citation as the client claims it. Re-validated before storage (§21.2)."""

    file_path: str = Field(min_length=1, max_length=1_000)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)


class ShareRequest(BaseModel):
    """``POST /repos/{id}/share`` — publish one answer.

    Every field is client-supplied, so every field is bounded. The citations
    are checked against the snapshot server-side; the caps here bound the work
    that check does, not the trust placed in the input.
    """

    question: str = Field(min_length=1, max_length=QUESTION_MAX_CHARS)
    answer: str = Field(min_length=1, max_length=SHARED_ANSWER_MAX_CHARS)
    citations: list[CitationIn] = Field(default_factory=list, max_length=SHARED_CITATIONS_MAX)
    model: str | None = Field(default=None, max_length=200)


class ShareCreated(BaseModel):
    """The permalink id. The URL is the frontend's to build (§21.3)."""

    id: UUID


class SharedAnswerOut(BaseModel):
    """``GET /shared/{id}`` — the public read.

    Carries the repo's name, URL and pinned commit so a reader with no account
    can still resolve citations, as GitHub blob links at that commit. That is
    what makes a permalink useful off this app, and it is sound only because a
    snapshot is frozen (§14.3).

    ``created_by`` is absent by design: who published an answer is the owner's
    business, not the reader's.
    """

    id: UUID
    question: str
    answer: str
    citations: list[Citation]
    model: str | None
    created_at: dt.datetime
    repo_name: str
    repo_url: str
    commit_sha: str | None

    @classmethod
    def from_row(cls, row: Any) -> SharedAnswerOut:
        raw = row["citations"]
        return cls(
            id=row["id"],
            question=row["question"],
            answer=row["answer"],
            citations=json.loads(raw) if isinstance(raw, str) else raw,
            model=row["model"],
            created_at=row["created_at"],
            repo_name=row["repo_name"],
            repo_url=row["repo_url"],
            commit_sha=row["commit_sha"],
        )


class ChecklistItemOut(BaseModel):
    """One step of the §22 onboarding checklist."""

    kind: str
    title: str
    detail: str
    file_path: str
    start_line: int
    end_line: int
    question: str


class ChecklistOut(BaseModel):
    """``GET /repos/{id}/checklist`` (§22.2).

    ``items`` is in reading order, not ranked order — see `build_checklist`.
    Fewer than five is normal and deliberate: a library has no entry point, and
    padding the list would teach the reader to skim it.
    """

    items: list[ChecklistItemOut]


class ConversationOut(BaseModel):
    """One entry in the §23.4 resume list."""

    id: UUID
    title: str
    n_turns: int
    created_at: dt.datetime
    updated_at: dt.datetime


class ConversationList(BaseModel):
    conversations: list[ConversationOut]


class TurnOut(BaseModel):
    """One stored exchange. No tool timeline — §23.1 does not keep one."""

    ordinal: int
    question: str
    answer: str
    citations: list[Citation]
    created_at: dt.datetime


class ConversationDetail(BaseModel):
    """``GET /repos/{id}/conversations/{cid}`` — everything needed to resume.

    Turns oldest-first, so a client renders them in the order they happened
    without sorting. Every turn carries its validated citations, so resuming
    costs no model call and no re-validation.
    """

    id: UUID
    title: str
    created_at: dt.datetime
    updated_at: dt.datetime
    turns: list[TurnOut]

    @classmethod
    def from_rows(cls, convo: Any, turn_rows: Sequence[Any]) -> ConversationDetail:
        return cls(
            id=convo["id"],
            title=convo["title"],
            created_at=convo["created_at"],
            updated_at=convo["updated_at"],
            turns=[
                TurnOut(
                    ordinal=r["ordinal"],
                    question=r["question"],
                    answer=r["answer"],
                    citations=[
                        Citation(**c)
                        for c in (
                            json.loads(r["citations"])
                            if isinstance(r["citations"], str)
                            else r["citations"]
                        )
                    ],
                    created_at=r["created_at"],
                )
                for r in turn_rows
            ],
        )


class TraceNode(BaseModel):
    """One symbol the walk reached, and how it got there."""

    depth: int
    kind: str | None
    name: str
    qualname: str
    file_path: str
    start_line: int
    end_line: int
    # The symbol one hop nearer the root. Together with `depth` this is the
    # path — a client can rebuild the chain without the server serialising one
    # per node, which on a wide graph is the same data quadratically.
    via: str | None


class TraceOut(BaseModel):
    """``GET /repos/{id}/trace`` (§24.2).

    ``nodes`` is ordered by depth then qualname, so truncation keeps the near
    neighbours — on a hot symbol the first hop is the answer.
    """

    root: SymbolRef
    direction: str
    max_depth: int
    nodes: list[TraceNode]
    truncated: bool


class DependencyUse(BaseModel):
    """One import site for a package."""

    dotted: str
    file_path: str
    start_line: int
    is_test: bool


class DependencyOut(BaseModel):
    """One third-party package this snapshot imports (§26.2).

    ``declared`` is a *normalised-name* match against the manifests and is
    honestly approximate: a distribution name and the module it ships need not
    agree (`PyYAML` ships `yaml`), so ``False`` means "no manifest row under
    this name", never "this package is undeclared".
    """

    module: str
    n_uses: int
    n_files: int
    declared: bool
    requirement: str | None
    sources: list[str]
    extras: list[str]

    @classmethod
    def from_row(cls, row: asyncpg.Record) -> DependencyOut:
        return cls(
            module=str(row["module"]),
            n_uses=int(row["n_uses"]),
            n_files=int(row["n_files"]),
            declared=bool(row["declared"]),
            requirement=row["requirement"],
            sources=list(row["sources"] or []),
            extras=list(row["extras"] or []),
        )


class UnusedDependency(BaseModel):
    """A manifest entry no import in the corpus reaches (§26.2)."""

    name: str
    requirement: str
    sources: list[str]
    extras: list[str]


class DependenciesOut(BaseModel):
    """``GET /repos/{id}/dependencies`` (§26.2).

    ``indexed`` is the §26.3 distinction and the reason this is not just a
    list: a snapshot ingested before migration 015 has no rows, and an empty
    ``packages`` would otherwise read as "this project stands on nothing" —
    which for any real repo is a confident lie.
    """

    indexed: bool
    include_tests: bool
    packages: list[DependencyOut]
    undeclared: list[str]
    unused: list[UnusedDependency]
    truncated: bool


class DependencyUsesOut(BaseModel):
    """``GET /repos/{id}/dependencies/{module}`` — the "and where?" half."""

    module: str
    include_tests: bool
    uses: list[DependencyUse]
    truncated: bool
