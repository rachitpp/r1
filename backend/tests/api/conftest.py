"""Networkless fixtures for the §8 API and §9 SSE tests.

Two substitutions, and nothing else is faked:

* **The connection.** ``FakeConn`` (see ``fakes.py``) answers the statements the
  API and the agent tools issue, out of dicts.
* **The model.** The Phase 3 scripted ``FakeChatModel`` is injected through the
  ``get_chat_model`` dependency, so the chat tests drive the *real* graph, tools,
  citation parser, and SSE adapter — everything except the provider call.

The lifespan is bypassed on purpose: it opens a real pool, connects to Redis, and
warms an 18-second model. State the app needs is set directly on ``app.state``.

Fixture wiring only. The fakes themselves, and the seed rows they serve, are in
``tests/api/fakes.py``.
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
from tests.api.fakes import (
    FILE_PATH,
    OTHER_USER_ID,
    USER_ID,
    FakeArq,
    FakeConn,
)


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
    to mint a session. ``None`` restores the real dependency, which is how the
    unauthenticated cases reach a genuine 401 instead of a faked one.

    The ``pop`` matters. These overrides are global to ``app``, so a test that
    requests two client fixtures wires the app twice and the last call wins. It
    used to only ever *add* the user override, which meant an ``anon_client``
    set up after a signed-in one silently inherited its session — an
    unauthenticated assertion that passes for the wrong reason. It now clears,
    so the two fixtures cannot be combined without the failure being obvious.
    (They still should not be combined: one app, one wiring. Seed the row
    instead, the way ``shares`` below is seeded.)
    """

    async def _get_conn() -> AsyncIterator[FakeConn]:
        yield conn

    app.dependency_overrides[deps.get_conn] = _get_conn
    app.dependency_overrides[deps.get_pool] = lambda: conn
    app.dependency_overrides[deps.get_arq] = lambda: arq
    app.dependency_overrides[deps.get_chat_model] = lambda: model
    if as_user is not None:
        app.dependency_overrides[deps.get_current_user] = lambda: conn.users[as_user]
    else:
        app.dependency_overrides.pop(deps.get_current_user, None)
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
