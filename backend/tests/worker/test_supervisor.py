"""The supervised worker loop (DECISIONS 2026-08-03).

Observed: the worker completed three jobs, sat idle for 54 minutes, then died
on a `TimeoutError` raised while polling Redis. `retry_on_error` covers a blip
*inside a job* — the retry is on the connection, so `run_job` recovers — but
ARQ's own poll and health-check loop is not covered by any of it, and an
exception there leaves `Worker.run()` and ends the process.

The symptom is the one RUNNING.md §6 calls the most common broken-looking
setup: a submitted repo sits at 0% forever with no error, because the thing
that would have written the error is the thing that is gone.
"""

from __future__ import annotations

from typing import Any

import pytest

from app import worker as worker_mod


class FakeWorker:
    """A worker whose `async_run` follows a script of outcomes."""

    instances: list[FakeWorker] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.closed = False
        FakeWorker.instances.append(self)

    async def async_run(self) -> None:
        # An exhausted script means "stop cleanly". Popping from an empty list
        # would raise IndexError, which the supervisor would dutifully treat as
        # a crash and retry forever — the test hanging rather than failing.
        outcome = FakeWorker.script.pop(0) if FakeWorker.script else None
        if outcome is not None:
            raise outcome

    async def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _fake_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeWorker.instances = []
    FakeWorker.script = []  # type: ignore[attr-defined]
    import arq.worker

    monkeypatch.setattr(arq.worker, "Worker", FakeWorker)
    # No real waiting: the backoff is a policy, not something to sit through.
    async def _no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(worker_mod.asyncio, "sleep", _no_sleep)


async def test_a_clean_stop_is_not_restarted() -> None:
    """`async_run` returning means shutdown was asked for and honoured."""
    FakeWorker.script = [None]  # type: ignore[attr-defined]
    await worker_mod._run_supervised()
    assert len(FakeWorker.instances) == 1


async def test_a_redis_timeout_while_polling_restarts_the_worker() -> None:
    """The exact failure seen: TimeoutError out of the poll loop."""
    FakeWorker.script = [TimeoutError("Timeout connecting to server"), None]  # type: ignore[attr-defined]
    await worker_mod._run_supervised()
    assert len(FakeWorker.instances) == 2, "it should have started a second worker"
    # The dead one is closed before the replacement is built, so its connection
    # pool cannot outlive it.
    assert FakeWorker.instances[0].closed is True


async def test_it_keeps_restarting_across_repeated_failures() -> None:
    FakeWorker.script = [  # type: ignore[attr-defined]
        TimeoutError("1"),
        ConnectionError("2"),
        RuntimeError("3"),
        None,
    ]
    await worker_mod._run_supervised()
    assert len(FakeWorker.instances) == 4


async def test_cancellation_is_not_a_failure_to_retry() -> None:
    """Ctrl-C and SIGTERM mean the operator wants it stopped."""
    FakeWorker.script = [__import__("asyncio").CancelledError()]  # type: ignore[attr-defined]
    with pytest.raises(__import__("asyncio").CancelledError):
        await worker_mod._run_supervised()
    assert len(FakeWorker.instances) == 1


async def test_a_close_that_also_fails_does_not_stop_the_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Closing talks to Redis, which is very likely what just broke."""

    class ExplodingClose(FakeWorker):
        async def close(self) -> None:
            raise ConnectionError("redis is still gone")

    import arq.worker

    monkeypatch.setattr(arq.worker, "Worker", ExplodingClose)
    FakeWorker.script = [TimeoutError("boom"), None]  # type: ignore[attr-defined]
    await worker_mod._run_supervised()
    assert len(FakeWorker.instances) == 2


async def test_each_restart_builds_fresh_redis_settings() -> None:
    """A settings object carries a connection pool; reusing the dead one's
    would restart the worker onto the socket that just failed."""
    FakeWorker.script = [TimeoutError("x"), None]  # type: ignore[attr-defined]
    await worker_mod._run_supervised()
    first, second = FakeWorker.instances
    assert first.kwargs["redis_settings"] is not second.kwargs["redis_settings"]
