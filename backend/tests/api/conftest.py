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
from collections.abc import AsyncIterator, Callable, Iterator
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
FAILED_REPO_ID = uuid.UUID("44444444-4444-4444-4444-444444444444")

# Two tenants (SPEC §13). USER_ID owns every seeded repo; OTHER_USER_ID owns
# nothing, which is what makes it useful — it is the caller every cross-tenant
# assertion is written from.
USER_ID = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
OTHER_USER_ID = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")

FILE_PATH = "pkg/auth.py"
FILE_CONTENT = "def verify_token(token):\n    return SECRET_SENTINEL_VALUE\n"


def _user_row(user_id: uuid.UUID, login: str) -> dict[str, Any]:
    """A `users` row as `queries.USER_COLUMNS` selects it."""
    return {
        "id": user_id,
        "github_id": 1 if user_id == USER_ID else 2,
        "login": login,
        "name": login.title(),
        "avatar_url": None,
        "created_at": "2026-07-29T00:00:00+00:00",
    }


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
            FAILED_REPO_ID: _repo_row(FAILED_REPO_ID, "owner/failed", "failed"),
        }
        self.repos[FAILED_REPO_ID]["error"] = "worker died"
        self.users: dict[uuid.UUID, dict[str, Any]] = {
            USER_ID: _user_row(USER_ID, "owner"),
            OTHER_USER_ID: _user_row(OTHER_USER_ID, "stranger"),
        }
        # §13.2 `user_repos`. Every seeded repo belongs to USER_ID and to nobody
        # else, so an OTHER_USER_ID request that reaches any of them is a
        # tenancy failure rather than a fixture gap.
        self.user_repos: set[tuple[uuid.UUID, uuid.UUID]] = {
            (USER_ID, repo_id) for repo_id in self.repos
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
        # §13.5 ownership join: (repo_id, user_id). Returns None for a repo that
        # exists but belongs to someone else, which is what makes the route 404.
        if "JOIN user_repos ur" in sql and "WHERE r.id = $1" in sql:
            repo_id, user_id = args[0], args[1]
            if (user_id, repo_id) not in self.user_repos:
                return None
            return self.repos.get(repo_id)
        if "FROM users WHERE id" in sql:
            return self.users.get(args[0])
        if "INSERT INTO users" in sql:
            github_id, login = args[0], args[1]
            existing = next(
                (u for u in self.users.values() if u["github_id"] == github_id), None
            )
            if existing is not None:
                existing["login"] = login
                return existing
            new_id = uuid.uuid4()
            self.users[new_id] = {
                "id": new_id,
                "github_id": github_id,
                "login": login,
                "name": args[2],
                "avatar_url": args[3],
                "created_at": "2026-07-29T00:00:00+00:00",
            }
            return self.users[new_id]
        if "FROM files WHERE repo_id = $1 AND path = $2" in sql:
            return self.files.get(str(args[1]))
        return None

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        if "JOIN user_repos ur" in sql and "WHERE ur.user_id = $1" in sql:
            user_id = args[0]
            return [
                row
                for repo_id, row in self.repos.items()
                if (user_id, repo_id) in self.user_repos
            ]
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
        if "INSERT INTO user_repos" in sql:
            self.user_repos.add((args[0], args[1]))
            return "INSERT 0 1"
        if "UPDATE repos" in sql and "status = $2" in sql:
            row = self.repos.get(args[0])
            if row is not None:
                row["status"] = args[1]
                if "error = NULL" in sql:
                    row["error"] = None
        return "UPDATE 1"


class FakeArq:
    """Records enqueues instead of touching Redis.

    Also serves the two commands the rate limiter uses (``incr``/``expire``),
    because the limiter reuses ARQ's Redis connection — so a fake that only
    knew about jobs would make every test run through the limiter's fail-open
    path and prove nothing about it.
    """

    def __init__(self) -> None:
        self.jobs: list[tuple[str, tuple[Any, ...]]] = []
        self.counters: dict[str, int] = {}

    async def enqueue_job(self, function: str, *args: Any, **kwargs: Any) -> None:
        self.jobs.append((function, args))
        return None

    async def incr(self, name: str) -> int:
        self.counters[name] = self.counters.get(name, 0) + 1
        return self.counters[name]

    async def expire(self, name: str, time: int) -> bool:
        return True

    async def ping(self) -> bool:
        return True


@pytest.fixture
def conn() -> FakeConn:
    return FakeConn()


def _rate_limit_layers() -> list[Any]:
    """Every RateLimitMiddleware instance in the built ASGI stack."""
    from app.api.middleware import RateLimitMiddleware
    from app.main import app

    found, node = [], app.middleware_stack
    while node is not None:
        if isinstance(node, RateLimitMiddleware):
            found.append(node)
        node = getattr(node, "app", None)
    return found


@pytest.fixture(autouse=True)
def rate_limit_rules() -> Iterator[Callable[[], None]]:
    """Reload the live rule table from settings, and always restore it after.

    The middleware stack is built once at import and reads its limits from
    settings *at that moment*, so a test that monkeypatches a limit has to push
    the new table in. Autouse for the restore half: without it, one test's
    tightened limit silently applies to every test that runs after it.
    """
    from app.api.ratelimit import rules_for
    from app.config import get_settings

    layers = _rate_limit_layers()
    original = [layer.rules for layer in layers]

    def reload() -> None:
        for layer in layers:
            layer.rules = rules_for(get_settings())

    try:
        yield reload
    finally:
        for layer, rules in zip(layers, original, strict=True):
            layer.rules = rules


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


def _wire(
    conn: FakeConn,
    arq: FakeArq,
    model: FakeChatModel,
    *,
    as_user: uuid.UUID | None,
) -> None:
    """Point the app's dependencies at the fakes.

    ``as_user`` overrides ``get_current_user`` so route tests do not each have
    to mint a session. ``None`` leaves the real dependency in place, which is
    how the unauthenticated cases reach a genuine 401 instead of a faked one.
    """

    async def _get_conn() -> AsyncIterator[FakeConn]:
        yield conn

    app.dependency_overrides[deps.get_conn] = _get_conn
    app.dependency_overrides[deps.get_pool] = lambda: conn
    app.dependency_overrides[deps.get_arq] = lambda: arq
    app.dependency_overrides[deps.get_chat_model] = lambda: model
    if as_user is not None:
        app.dependency_overrides[deps.get_current_user] = lambda: conn.users[as_user]
    app.state.pool = conn
    app.state.arq = arq
    app.state.embedder_ready = False


@pytest.fixture
async def client(
    conn: FakeConn, arq: FakeArq, scripted_model: FakeChatModel
) -> AsyncIterator[httpx.AsyncClient]:
    """The app with fake conn/queue/model wired in, lifespan bypassed.

    ``get_pool`` returns the same fake connection as ``get_conn``: chat takes
    the pool so it can check connections out per tool call, and
    :func:`app.db.pool.acquire` yields a non-pool source unchanged — so one fake
    satisfies both without pretending to be a pool.

    ``app.state`` is populated too, because the middleware and the operational
    endpoints read it directly rather than through a dependency, and the
    lifespan that would normally fill it is bypassed here.

    **Signed in as ``USER_ID``**, who owns every seeded repo. Since V1 every
    ``/repos`` route requires a user, and making each of these tests perform a
    sign-in would test the fixture, not the route.
    """
    _wire(conn, arq, scripted_model, as_user=USER_ID)
    try:
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            yield c
    finally:
        app.dependency_overrides.clear()
        app.state.pool = None
        app.state.arq = None


@pytest.fixture
async def other_client(
    conn: FakeConn, arq: FakeArq, scripted_model: FakeChatModel
) -> AsyncIterator[httpx.AsyncClient]:
    """A second, signed-in tenant who owns nothing (SPEC §13.5).

    Shares the same ``conn`` fixture as ``client``, so both see one database and
    a cross-tenant test is asking a real question about the same rows.
    """
    _wire(conn, arq, scripted_model, as_user=OTHER_USER_ID)
    try:
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            yield c
    finally:
        app.dependency_overrides.clear()
        app.state.pool = None
        app.state.arq = None


@pytest.fixture
async def anon_client(
    conn: FakeConn, arq: FakeArq, scripted_model: FakeChatModel
) -> AsyncIterator[httpx.AsyncClient]:
    """No session at all — ``get_current_user`` is *not* overridden.

    The only fixture that exercises the real dependency, so a 401 here means
    the route is genuinely protected rather than that a fake said so.
    """
    _wire(conn, arq, scripted_model, as_user=None)
    try:
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            yield c
    finally:
        app.dependency_overrides.clear()
        app.state.pool = None
        app.state.arq = None
