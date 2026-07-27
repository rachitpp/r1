"""Pydantic v2 request/response models (SPEC §8).

Shapes here are the frontend's contract; keep them exactly as §8 specifies. The
SSE event payloads are *not* here — they are transport-level dicts built in
:mod:`app.api.chat_stream` against §9.
"""

from __future__ import annotations

import datetime as dt
from uuid import UUID

import asyncpg
from pydantic import BaseModel, Field


class RepoCreate(BaseModel):
    """``POST /repos`` body. Validation of the URL itself is §8's 422 path."""

    url: str


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


class FileOut(BaseModel):
    path: str
    content: str
    n_lines: int


class ChatRequest(BaseModel):
    """``POST /repos/{id}/chat`` body.

    Chat is POST, not EventSource: questions do not belong in URLs, and the
    frontend consumes the stream with fetch + ReadableStream (§8).
    """

    question: str = Field(min_length=1)
