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

import datetime as dt
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

# Sources are keyed by URL and their ids are derived, so a fixture never has to
# thread one around: the same URL always yields the same source id.
def _source_id_for(url: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, url)


FILE_PATH = "pkg/auth.py"
FILE_CONTENT = "def verify_token(token):\n    return SECRET_SENTINEL_VALUE\n"
TEST_FILE_PATH = "tests/test_auth.py"

# A three-module symbol graph for the §18 rollup views. Small enough to assert
# on exactly, and shaped so the interesting cases are all present: one module
# every other module depends on (`pkg/auth.py`, fan-in 2), a same-file edge that
# must NOT appear in the rollup, and a test file whose edges are excluded unless
# `include_tests` is set.
MODULE_NODE_ROWS: list[dict[str, Any]] = [
    {"path": FILE_PATH, "n_symbols": 2, "fan_in": 2, "fan_out": 0},
    {"path": "pkg/client.py", "n_symbols": 3, "fan_in": 0, "fan_out": 1},
    {"path": "pkg/api.py", "n_symbols": 1, "fan_in": 0, "fan_out": 1},
]
MODULE_EDGE_ROWS: list[dict[str, Any]] = [
    {"from_path": "pkg/client.py", "to_path": FILE_PATH, "kind": "calls", "weight": 4},
    {"from_path": "pkg/api.py", "to_path": FILE_PATH, "kind": "imports", "weight": 1},
]
# One implementation symbol reached by two tests, plus a second symbol reached
# by none — grouping is only proven by a symbol that has more than one test.
COVERAGE_ROWS: list[dict[str, Any]] = [
    {
        "name": "verify_token",
        "qualname": "pkg.auth.verify_token",
        "kind": "function",
        "start_line": 1,
        "end_line": 2,
        "ref_qualname": "tests.test_auth.test_verify_token_ok",
        "ref_file_path": TEST_FILE_PATH,
        "ref_line": 11,
    },
    {
        "name": "verify_token",
        "qualname": "pkg.auth.verify_token",
        "kind": "function",
        "start_line": 1,
        "end_line": 2,
        "ref_qualname": "tests.test_auth.test_verify_token_expired",
        "ref_file_path": TEST_FILE_PATH,
        "ref_line": 19,
    },
    {
        "name": "Signer",
        "qualname": "pkg.auth.Signer",
        "kind": "class",
        "start_line": 5,
        "end_line": 9,
        "ref_qualname": "tests.test_auth.test_signer",
        "ref_file_path": TEST_FILE_PATH,
        "ref_line": 30,
    },
]
COVERS_ROWS: list[dict[str, Any]] = [
    {
        "ref_qualname": "pkg.auth.verify_token",
        "ref_file_path": FILE_PATH,
        "ref_line": 1,
    }
]

# §20 history. Shaped so every query-time decision is provable: two commits on
# FILE_PATH at different times (ordering), one merge that also touched it
# (excluded by default), and one commit on a different file (path scoping).
HISTORY_ROWS: list[dict[str, Any]] = [
    {
        "sha": "c0ffee1",
        "author_name": "Ada",
        "author_email": "ada@example.com",
        "authored_at": dt.datetime(2026, 7, 3, tzinfo=dt.UTC),
        "subject": "auth: reject expired tokens",
        "body": "The check was there and never ran.",
        "is_merge": False,
        "insertions": 12,
        "deletions": 3,
        "_path": FILE_PATH,
    },
    {
        "sha": "beef002",
        "author_name": "Grace",
        "author_email": None,
        "authored_at": dt.datetime(2026, 7, 1, tzinfo=dt.UTC),
        "subject": "auth: first cut",
        "body": None,
        "is_merge": False,
        "insertions": 40,
        "deletions": 0,
        "_path": FILE_PATH,
    },
    {
        "sha": "merge003",
        "author_name": "Ada",
        "author_email": "ada@example.com",
        "authored_at": dt.datetime(2026, 7, 2, tzinfo=dt.UTC),
        "subject": "Merge branch 'auth'",
        "body": None,
        "is_merge": True,
        "insertions": 0,
        "deletions": 0,
        "_path": FILE_PATH,
    },
    {
        "sha": "d0cs004",
        "author_name": "Grace",
        "author_email": None,
        "authored_at": dt.datetime(2026, 7, 4, tzinfo=dt.UTC),
        "subject": "docs: unrelated",
        "body": None,
        "is_merge": False,
        "insertions": 5,
        "deletions": 1,
        "_path": "README.md",
    },
]


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


def _repo_row(
    repo_id: uuid.UUID, name: str, status: str, *, strategy: str = "ast"
) -> dict[str, Any]:
    """A snapshot joined to its source, as `queries.SNAPSHOT_COLUMNS` selects it.

    Post-§14 a "repo" in the API is a snapshot, and the join hands back the
    source's `url`/`name` beside the snapshot's own columns — so this dict is
    still exactly what `RepoOut.from_row` consumes (§14.7).

    A freshly queued repo has nothing counted yet; anything further along carries
    the same fixed numbers, which is all the progress assertions need.
    """
    fresh = status == "queued"
    return {
        "id": repo_id,
        "source_id": _source_id_for(f"https://github.com/{name}"),
        "strategy": strategy,
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
        # §14.2 `repo_sources`, keyed by id. Seeded from the snapshots above so
        # a lookup by source_id finds the URL the snapshot was created from.
        self.sources: dict[uuid.UUID, dict[str, Any]] = {
            r["source_id"]: {"id": r["source_id"], "url": r["url"], "name": r["name"]}
            for r in self.repos.values()
        }
        self.files: dict[str, dict[str, Any]] = {
            FILE_PATH: {
                "path": FILE_PATH,
                "content": FILE_CONTENT,
                "n_lines": len(FILE_CONTENT.splitlines()),
            }
        }
        # §19. Keyed by snapshot exactly as the real primary key is, so the
        # "claim exactly once" assertion is testing the same rule the database
        # enforces rather than a fixture that happens to agree with it.
        self.overviews: dict[uuid.UUID, dict[str, Any]] = {}
        # §20. Keyed by snapshot and *absent* for INDEXING_REPO_ID, so the
        # "indexed" flag has a repo to be false for — the state every snapshot
        # ingested before §20 is actually in.
        self.history: dict[uuid.UUID, list[dict[str, Any]]] = {
            REPO_ID: [dict(r) for r in HISTORY_ROWS]
        }
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    # --- asyncpg surface ---------------------------------------------------

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        # §20.4: "was history indexed at all", asked only when the list is empty.
        if "EXISTS (SELECT 1 FROM commits" in sql:
            return {"present": bool(self.history.get(args[0]))}
        # --- §14.2 sources and snapshots ---------------------------------
        if "INSERT INTO repo_sources" in sql:
            url, name = str(args[0]), str(args[1])
            source_id = _source_id_for(url)
            if source_id in self.sources:
                return None  # ON CONFLICT DO NOTHING
            self.sources[source_id] = {"id": source_id, "url": url, "name": name}
            return {"id": source_id}
        if "FROM repo_sources WHERE url" in sql:
            return {"id": _source_id_for(str(args[0]))}
        if "INSERT INTO repo_snapshots" in sql:
            source_id, strategy = args[0], str(args[1])
            new_id = uuid.uuid4()
            source = self.sources[source_id]
            url, name = str(source["url"]), str(source["name"])
            row = _repo_row(new_id, name, "queued", strategy=strategy)
            row["url"], row["source_id"] = url, source_id
            self.repos[new_id] = row
            return {"id": new_id}
        # newest_snapshot_for_source: (source_id, strategy)
        if "WHERE sn.source_id = $1" in sql:
            matches = [
                r
                for r in self.repos.values()
                if r["source_id"] == args[0] and r["strategy"] == str(args[1])
            ]
            return matches[-1] if matches else None
        # §13.5 ownership join. Returns None for a snapshot that exists but
        # belongs to someone else, which is what makes the route 404.
        if "JOIN user_repos ur" in sql and "WHERE sn.id = $1" in sql:
            snapshot_id, user_id = args[0], args[1]
            if (user_id, snapshot_id) not in self.user_repos:
                return None
            return self.repos.get(snapshot_id)
        if "FROM repo_snapshots sn JOIN repo_sources s" in sql and "WHERE sn.id = $1" in sql:
            return self.repos.get(args[0])
        if "FROM users WHERE id" in sql:
            return self.users.get(args[0])
        if "FROM users WHERE login" in sql:
            return next(
                ({"id": u["id"]} for u in self.users.values() if u["login"] == args[0]),
                None,
            )
        if "FROM users WHERE github_id" in sql:
            return next(
                (
                    {"id": u["id"]}
                    for u in self.users.values()
                    if u["github_id"] == args[0]
                ),
                None,
            )
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
        if "FROM files WHERE snapshot_id = $1 AND path = $2" in sql:
            return self.files.get(str(args[1]))
        # --- §19 overview -------------------------------------------------
        if "INSERT INTO snapshot_overviews" in sql:
            snapshot_id = args[0]
            if snapshot_id in self.overviews:
                return None  # ON CONFLICT DO NOTHING — somebody already holds it
            self.overviews[snapshot_id] = {
                "snapshot_id": snapshot_id,
                "status": "generating",
                "body": None,
                "citations": "[]",
                "model": None,
                "error": None,
                "created_at": "2026-07-31T00:00:00+00:00",
            }
            return {"snapshot_id": snapshot_id}
        if "FROM snapshot_overviews WHERE snapshot_id" in sql:
            return self.overviews.get(args[0])
        return None

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        # --- §20 history ---------------------------------------------------
        # Both shapes honour `include_merges` and the limit, for the same
        # reason the §18 views honour `include_tests`: a fake that ignored the
        # flag would pass the assertion without proving it reached SQL.
        if "FROM commit_files cf" in sql:
            snapshot_id, path, include_merges, limit = args[0], str(args[1]), args[2], args[3]
            if snapshot_id not in self.history:
                return []
            rows = [
                r
                for r in self.history[snapshot_id]
                if r["_path"] == path and (include_merges or not r["is_merge"])
            ]
            rows.sort(key=lambda r: (r["authored_at"], r["sha"]), reverse=True)
            return [{k: v for k, v in r.items() if k != "_path"} for r in rows][:limit]
        if "LEFT JOIN commit_files cf" in sql:
            snapshot_id, include_merges, limit = args[0], args[1], args[2]
            if snapshot_id not in self.history:
                return []
            rows = [
                r
                for r in self.history[snapshot_id]
                if include_merges or not r["is_merge"]
            ]
            rows.sort(key=lambda r: (r["authored_at"], r["sha"]), reverse=True)
            return [{k: v for k, v in r.items() if k != "_path"} for r in rows][:limit]
        # --- §18 graph views ----------------------------------------------
        # `include_tests` ($2) is honoured rather than ignored: the tests assert
        # the flag reaches SQL, and a fake that returned the same rows either
        # way would pass that assertion while proving nothing.
        if "cross_edges" in sql:
            rows = MODULE_NODE_ROWS if not args[1] else MODULE_NODE_ROWS + [
                {"path": TEST_FILE_PATH, "n_symbols": 3, "fan_in": 0, "fan_out": 3}
            ]
            return [dict(r) for r in rows[: args[2]]]
        if "GROUP BY f.file_path, t.file_path, e.kind" in sql:
            rows = MODULE_EDGE_ROWS if not args[1] else MODULE_EDGE_ROWS + [
                {
                    "from_path": TEST_FILE_PATH,
                    "to_path": FILE_PATH,
                    "kind": "calls",
                    "weight": 3,
                }
            ]
            return [dict(r) for r in rows[: args[2]]]
        if "AND t.is_test" in sql and "impl.file_path = $2" in sql:
            path = str(args[1])
            return [dict(r) for r in COVERAGE_ROWS if path == FILE_PATH][: args[2]]
        if "AND NOT impl.is_test" in sql:
            path = str(args[1])
            return [dict(r) for r in COVERS_ROWS if path == TEST_FILE_PATH][: args[2]]
        if "JOIN user_repos ur" in sql and "WHERE ur.user_id = $1" in sql:
            user_id = args[0]
            return [
                row
                for snapshot_id, row in self.repos.items()
                if (user_id, snapshot_id) in self.user_repos
            ]
        if "FROM repo_snapshots sn JOIN repo_sources s" in sql:
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
        if "DELETE FROM snapshot_overviews" in sql:
            row = self.overviews.get(args[0])
            if row is not None and row["status"] == "failed":
                del self.overviews[args[0]]
                return "DELETE 1"
            return "DELETE 0"
        if "UPDATE snapshot_overviews" in sql and "status = 'ready'" in sql:
            row = self.overviews.get(args[0])
            if row is not None:
                row.update(
                    status="ready", body=args[1], citations=args[2], model=args[3]
                )
            return "UPDATE 1"
        if "UPDATE snapshot_overviews" in sql and "status = 'failed'" in sql:
            row = self.overviews.get(args[0])
            if row is not None:
                row.update(status="failed", error=args[1])
            return "UPDATE 1"
        if "UPDATE repo_snapshots" in sql and "status = $2" in sql:
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
