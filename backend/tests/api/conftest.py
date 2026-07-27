"""Networkless fixtures for the §8 API and §9 SSE tests.

Two substitutions, and nothing else is faked:

* **The connection.** ``FakeConn`` answers the handful of statements the API and
  the agent tools issue, out of dicts. The alternative — a live Postgres — would
  make the route tests an integration suite, and Phase 2 already has one of
  those for the SQL itself.
* **The model.** The Phase 3 scripted ``FakeChatModel`` is injected through the
  ``get_chat_model`` dependency, so the chat tests drive the *real* graph, tools,
  citation parser, and SSE adapter — everything except the provider call.

The lifespan is bypassed on purpose: it opens a real pool, connects to Redis, and
warms an 18-second model. State the app needs is set directly on ``app.state``.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from httpx import ASGITransport
from langchain_core.messages import AIMessage

from app.api import deps
from app.main import app
from tests.agent.test_graph import FakeChatModel

REPO_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
INDEXING_REPO_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
UNKNOWN_REPO_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")

FILE_PATH = "pkg/auth.py"
FILE_CONTENT = "def verify_token(token):\n    return SECRET_SENTINEL_VALUE\n"


def _repo_row(repo_id: uuid.UUID, name: str, status: str) -> dict[str, Any]:
    """A `repos` row as `queries.REPO_COLUMNS` selects it.

    A freshly queued repo has nothing counted yet; anything further along carries
    the same fixed numbers, which is all the progress assertions need.
    """
    fresh = status == "queued"
    return {
        "id": repo_id,
        "url": f"https://github.com/{name}",
        "name": name,
        "status": status,
        "error": None,
        "head_sha": None if fresh else "abc123",
        "default_branch": None if fresh else "main",
        "files_total": 0 if fresh else 3,
        "files_parsed": 0 if fresh else 3,
        "chunks_total": 0 if fresh else 9,
        "chunks_embedded": 0 if fresh else 9,
        "created_at": "2026-07-27T00:00:00+00:00",
    }


class FakeConn:
    """The narrow slice of asyncpg the API and read-only tools actually use.

    Statements are routed by substring rather than parsed: these are our own
    queries, in our own repo, and a routing table that goes stale is a loud test
    failure rather than a silent wrong answer.
    """

    def __init__(self) -> None:
        self.repos: dict[uuid.UUID, dict[str, Any]] = {
            REPO_ID: _repo_row(REPO_ID, "owner/ready", "ready"),
            INDEXING_REPO_ID: _repo_row(INDEXING_REPO_ID, "owner/indexing", "embedding"),
        }
        self.files: dict[str, dict[str, Any]] = {
            FILE_PATH: {
                "path": FILE_PATH,
                "content": FILE_CONTENT,
                "n_lines": len(FILE_CONTENT.splitlines()),
            }
        }
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    # --- asyncpg surface ---------------------------------------------------

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        if "INSERT INTO repos" in sql:
            url, name = str(args[0]), str(args[1])
            for row in self.repos.values():
                if row["url"] == url:
                    return None  # ON CONFLICT DO NOTHING
            new_id = uuid.uuid4()
            self.repos[new_id] = _repo_row(new_id, name, "queued")
            self.repos[new_id]["url"] = url
            return {"id": new_id}
        if "FROM repos WHERE url" in sql:
            return next(
                ({"id": r["id"]} for r in self.repos.values() if r["url"] == args[0]),
                None,
            )
        if "FROM repos WHERE id" in sql:
            return self.repos.get(args[0])
        if "FROM files WHERE repo_id = $1 AND path = $2" in sql:
            return self.files.get(str(args[1]))
        return None

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        if "FROM repos ORDER BY created_at" in sql:
            return list(self.repos.values())
        if "path = ANY" in sql:
            wanted = set(args[1])
            return [
                {"path": f["path"], "n_lines": f["n_lines"]}
                for f in self.files.values()
                if f["path"] in wanted
            ]
        if "SELECT path FROM files" in sql or "SELECT path, n_lines FROM files" in sql:
            return [
                {"path": f["path"], "n_lines": f["n_lines"]} for f in self.files.values()
            ]
        return []

    async def fetchval(self, sql: str, *args: Any) -> int:
        return 0

    async def execute(self, sql: str, *args: Any) -> str:
        self.executed.append((sql, args))
        if "UPDATE repos" in sql and "status = $2" in sql:
            row = self.repos.get(args[0])
            if row is not None:
                row["status"] = args[1]
        return "UPDATE 1"


class FakeArq:
    """Records enqueues instead of touching Redis."""

    def __init__(self) -> None:
        self.jobs: list[tuple[str, tuple[Any, ...]]] = []

    async def enqueue_job(self, function: str, *args: Any, **kwargs: Any) -> None:
        self.jobs.append((function, args))
        return None


@pytest.fixture
def conn() -> FakeConn:
    return FakeConn()


@pytest.fixture
def arq() -> FakeArq:
    return FakeArq()


@pytest.fixture
def scripted_model() -> FakeChatModel:
    """One `read_file` call, then an answer carrying a valid citation.

    `read_file` rather than `search_code` on purpose: search would load the real
    embedding model, and the tool whose result must *not* leak a code body is the
    one that returns a code body.
    """
    return FakeChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_file",
                        "args": {"path": FILE_PATH, "start_line": 1, "end_line": 2},
                        "id": "call-1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(
                content=(
                    f"Tokens are verified in [{FILE_PATH}:1-2], and nowhere else. "
                    "A fabricated path [made/up.py:1-2] must be dropped."
                )
            ),
        ],
        calls=[],
    )


@pytest.fixture
async def client(
    conn: FakeConn, arq: FakeArq, scripted_model: FakeChatModel
) -> AsyncIterator[httpx.AsyncClient]:
    """The app with fake conn/queue/model wired in, lifespan bypassed."""

    async def _get_conn() -> AsyncIterator[FakeConn]:
        yield conn

    app.dependency_overrides[deps.get_conn] = _get_conn
    app.dependency_overrides[deps.get_arq] = lambda: arq
    app.dependency_overrides[deps.get_chat_model] = lambda: scripted_model
    try:
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            yield c
    finally:
        app.dependency_overrides.clear()
