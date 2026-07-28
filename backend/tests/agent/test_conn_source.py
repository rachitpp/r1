"""Connections are borrowed per tool call, not held for the whole run.

This is the fix that matters most under load (DECISIONS 2026-07-28). An agent
run lasts as long as the model does; before this, its connection was checked out
for that entire span, so ``CHAT_MAX_CONCURRENCY`` answers in flight could empty
a pool of 20 and stall every other endpoint in the process — including the
progress polling that tells a user their repo is still indexing.

What is asserted here is the checkout *pattern*, not just that queries work: a
test that only checked results would pass equally well with the old behaviour.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import asyncpg
import pytest
from langchain_core.messages import AIMessage

from app.agent.graph import build_graph, repo_facts
from app.db.pool import acquire
from tests.agent.test_graph import FakeChatModel, FakeConn

REPO_ID = uuid.uuid4()


class CountingPool:
    """A stand-in pool that records how long each connection is held.

    Not an ``asyncpg.Pool`` subclass, which means :func:`app.db.pool.acquire`
    treats it as a bare connection — so the counting is done by hand here, and
    the test below patches the isinstance check rather than faking a pool.
    """

    def __init__(self) -> None:
        self.acquired = 0
        self.released = 0
        self.conn = FakeConn()

    def acquire(self) -> Any:
        pool = self

        class Ctx:
            async def __aenter__(self) -> Any:
                pool.acquired += 1
                return pool.conn

            async def __aexit__(self, *_exc: Any) -> bool:
                pool.released += 1
                return False

        return Ctx()

    @property
    def held(self) -> int:
        return self.acquired - self.released


@pytest.fixture
def pool(monkeypatch: pytest.MonkeyPatch) -> CountingPool:
    """A CountingPool that ``acquire()`` will treat as a real pool."""
    counting = CountingPool()
    real_isinstance = isinstance

    def patched(obj: Any, klass: Any) -> bool:
        if klass is asyncpg.Pool and obj is counting:
            return True
        return real_isinstance(obj, klass)

    monkeypatch.setattr("app.db.pool.isinstance", patched, raising=False)
    return counting


async def test_a_bare_connection_is_passed_through_untouched() -> None:
    """CLIs and tests own one connection; acquire must not demand a pool."""
    conn = FakeConn()
    async with acquire(conn) as got:
        assert got is conn


async def test_a_pool_checkout_is_released_at_the_end_of_the_block(
    pool: CountingPool,
) -> None:
    async with acquire(pool) as conn:
        assert conn is pool.conn
        assert pool.held == 1
    assert pool.held == 0
    assert pool.released == 1


async def test_a_checkout_is_released_even_when_the_block_raises(
    pool: CountingPool,
) -> None:
    with pytest.raises(RuntimeError):
        async with acquire(pool):
            raise RuntimeError("boom")
    assert pool.held == 0


async def test_repo_facts_does_not_keep_its_connection(pool: CountingPool) -> None:
    await repo_facts(pool, REPO_ID)
    assert pool.acquired == 1
    assert pool.held == 0


async def test_each_tool_call_borrows_and_returns_a_connection(
    pool: CountingPool,
) -> None:
    """Two tool calls, two short checkouts — and nothing held in between."""
    model = FakeChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "list_directory",
                        "args": {"path": ""},
                        "id": "call-1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "list_directory",
                        "args": {"path": "pkg"},
                        "id": "call-2",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="Done."),
        ],
        calls=[],
    )
    app = build_graph(model, pool, REPO_ID)
    await app.ainvoke(
        {
            "repo_id": str(REPO_ID),
            "question": "what is in here?",
            "messages": [],
            "tool_calls_used": 0,
            "citations": [],
        }
    )

    assert pool.acquired == 2, "one checkout per tool call"
    assert pool.held == 0, "nothing still checked out after the run"


async def test_the_connection_is_free_while_the_model_is_thinking(
    pool: CountingPool,
) -> None:
    """The whole point: model latency must not be connection-holding time.

    The fake model sleeps where a real one would be generating, and the pool is
    asserted empty at that exact moment.
    """
    observed: list[int] = []

    class SlowModel(FakeChatModel):
        async def _agenerate(  # type: ignore[override]
            self, messages: Any, stop: Any = None, run_manager: Any = None, **kw: Any
        ) -> Any:
            observed.append(pool.held)
            await asyncio.sleep(0)
            return await super()._agenerate(messages, stop, run_manager, **kw)

    model = SlowModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "list_directory",
                        "args": {"path": ""},
                        "id": "call-1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="Done."),
        ],
        calls=[],
    )
    app = build_graph(model, pool, REPO_ID)
    await app.ainvoke(
        {
            "repo_id": str(REPO_ID),
            "question": "q",
            "messages": [],
            "tool_calls_used": 0,
            "citations": [],
        }
    )

    assert observed, "the model was never called"
    assert observed == [0] * len(observed), "a connection was held across a model call"
