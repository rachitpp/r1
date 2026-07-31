"""Pydantic v2 request/response models (SPEC §8).

Shapes here are the frontend's contract; keep them exactly as §8 specifies. The
SSE event payloads are *not* here — they are transport-level dicts built in
:mod:`app.api.chat_stream` against §9.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from uuid import UUID

import asyncpg
from pydantic import BaseModel, Field

from app.config import QUESTION_MAX_CHARS, REPO_URL_MAX_CHARS


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


class ChatRequest(BaseModel):
    """``POST /repos/{id}/chat`` body.

    Chat is POST, not EventSource: questions do not belong in URLs, and the
    frontend consumes the stream with fetch + ReadableStream (§8).

    The length cap is not decoration: this string goes straight into a model
    context billed per token, so an unbounded field here is an unbounded bill.
    """

    question: str = Field(min_length=1, max_length=QUESTION_MAX_CHARS)
