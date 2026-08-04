"""Pooled-connection liveness (DECISIONS 2026-08-03).

The failure being guarded is specific and was observed: a managed Postgres
reaps an idle connection, asyncpg keeps reporting ``is_closed() is False``
because nothing has tried to write to it, the pool lends it out, and the
caller's query dies with ``ConnectionDoesNotExistError`` — reaching a user as
a 500 and a page reading "Can't reach the API".

These use a fake pool rather than a real one. The behaviour under test is
*ours* — probe, discard, retake — and a test that needed a live database to
kill a connection at the right moment would be a flake generator.
"""

from __future__ import annotations

from typing import Any

import asyncpg
import pytest

from app.db import pool as pool_mod


class FakeConn:
    """A connection that is alive, politely closed, or silently dead."""

    def __init__(self, *, closed: bool = False, dead: bool = False) -> None:
        self._closed = closed
        self._dead = dead
        self.pings = 0
        self.used = False

    def is_closed(self) -> bool:
        return self._closed

    async def close(self, timeout: float | None = None) -> None:
        self._closed = True

    async def fetchval(self, sql: str, *args: Any, timeout: float | None = None) -> int:
        self.pings += 1
        if self._dead:
            # Exactly what a reaped connection raises on first use.
            raise asyncpg.exceptions.ConnectionDoesNotExistError(
                "connection was closed in the middle of operation"
            )
        return 1


class _Checkout:
    """What `Pool.acquire()` returns: an async context manager, like asyncpg's."""

    def __init__(self, pool: FakePool) -> None:
        self._pool = pool
        self._conn: FakeConn | None = None

    async def __aenter__(self) -> FakeConn:
        self._pool.acquired += 1
        self._conn = self._pool._conns.pop(0)
        return self._conn

    async def __aexit__(self, *exc: Any) -> bool:
        assert self._conn is not None
        self._pool.released.append(self._conn)
        return False


class FakePool:
    """Hands out a scripted sequence of connections."""

    def __init__(self, conns: list[FakeConn]) -> None:
        self._conns = list(conns)
        self.acquired = 0
        self.released: list[FakeConn] = []

    def acquire(self) -> _Checkout:
        return _Checkout(self)


@pytest.fixture(autouse=True)
def _pool_is_a_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    """`acquire` dispatches on isinstance(source, asyncpg.Pool).

    Also rewinds the module's idle clock, so probing is armed. Probes are gated
    on a gap in traffic (`DB_POOL_PING_AFTER_IDLE_S`), and a test suite runs
    with no gaps at all.
    """
    monkeypatch.setattr(
        pool_mod.asyncpg, "Pool", FakePool
    )  # type: ignore[arg-type]
    monkeypatch.setattr(pool_mod, "_last_activity", 0.0)


async def test_live_connection_is_handed_over_after_one_probe() -> None:
    live = FakeConn()
    p = FakePool([live])
    async with pool_mod.acquire(p) as conn:
        conn.used = True
    assert live.pings == 1
    assert live.used is True
    assert p.acquired == 1
    assert p.released == [live]


async def test_a_dead_connection_is_discarded_and_another_taken() -> None:
    """The whole point: the caller never sees the reaped connection."""
    dead, live = FakeConn(dead=True), FakeConn()
    p = FakePool([dead, live])
    async with pool_mod.acquire(p) as conn:
        assert conn is live
    assert p.acquired == 2
    # Closed, which is what makes the pool replace rather than re-lend it.
    assert dead.is_closed() is True
    assert live.is_closed() is False


async def test_a_closed_connection_is_not_probed_at_all() -> None:
    """`is_closed()` is free; a round trip is not."""
    closed, live = FakeConn(closed=True), FakeConn()
    p = FakePool([closed, live])
    async with pool_mod.acquire(p) as conn:
        assert conn is live
    assert closed.pings == 0


async def test_it_gives_up_rather_than_retrying_forever() -> None:
    """Two dead in a row means the database is gone, not unlucky.

    The last connection is still yielded: the caller's own query then produces
    the real error, which is a better diagnostic than one invented here.
    """
    a, b = FakeConn(dead=True), FakeConn(dead=True)
    p = FakePool([a, b])
    async with pool_mod.acquire(p) as conn:
        assert conn is b
    assert p.acquired == 2


async def test_probing_can_be_switched_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """A local Postgres over a unix socket does not need the tax."""
    off = pool_mod.get_settings().model_copy(
        update={"DB_POOL_PING_ON_ACQUIRE": False}
    )
    monkeypatch.setattr(pool_mod, "get_settings", lambda: off)
    live = FakeConn()
    p = FakePool([live])
    async with pool_mod.acquire(p) as conn:
        assert conn is live
    assert live.pings == 0


async def test_a_bare_connection_is_passed_through_untouched() -> None:
    """The CLIs and the fake conn in the API tests own their own connection."""
    conn = FakeConn()
    async with pool_mod.acquire(conn) as yielded:
        assert yielded is conn
    assert conn.pings == 0


async def test_the_connection_is_released_even_when_the_block_raises() -> None:
    live = FakeConn()
    p = FakePool([live])
    with pytest.raises(RuntimeError):
        async with pool_mod.acquire(p):
            raise RuntimeError("caller blew up")
    assert p.released == [live]


async def test_a_busy_pool_is_not_probed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The measured reason this gate exists: a probe costs ~256 ms here.

    Under continuous traffic the connection was in use moments ago, so paying a
    round trip to ask whether it still works is a 50% latency tax against a risk
    that has not had time to materialise.
    """
    import time as _time

    monkeypatch.setattr(pool_mod, "_last_activity", _time.monotonic())
    live = FakeConn()
    p = FakePool([live])
    async with pool_mod.acquire(p) as conn:
        assert conn is live
    assert live.pings == 0


async def test_activity_is_recorded_so_the_next_checkout_skips_the_probe() -> None:
    first, second = FakeConn(), FakeConn()
    p = FakePool([first, second])
    async with pool_mod.acquire(p):
        pass
    async with pool_mod.acquire(p):
        pass
    # The first checkout was after a quiet spell and probed; the second follows
    # immediately and must not.
    assert first.pings == 1
    assert second.pings == 0
