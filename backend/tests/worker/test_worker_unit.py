"""Worker behaviour that does not need Redis: failure capture and sweep scope.

The sweep's *effect* on real rows is the integration test's job
(``test_worker_integration.py``); what is checked here is which states it claims,
because that choice is easy to widen by accident and expensive when wrong — a
sweep that also claimed ``queued`` would fail every repo waiting in a queue that
was working perfectly.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app import worker
from app.db import queries
from app.exceptions import CloneError


class RecordingConn:
    """Captures statements and arguments instead of executing them."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        self.calls.append((sql, args))
        return [{"id": uuid.uuid4()}]

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        """Non-None so `claim_snapshot` succeeds (SPEC §15.4).

        A worker that cannot take the lease returns early without ingesting, so
        a fake that refused the claim would make every test here assert nothing.
        """
        self.calls.append((sql, args))
        return {"id": args[0] if args else uuid.uuid4()}

    async def execute(self, sql: str, *args: Any) -> str:
        self.calls.append((sql, args))
        return "UPDATE 1"


class FakePool:
    """Hands out one recording connection through the `async with` protocol."""

    def __init__(self, conn: RecordingConn) -> None:
        self.conn = conn

    def acquire(self) -> FakePool:
        return self

    async def __aenter__(self) -> RecordingConn:
        return self.conn

    async def __aexit__(self, *exc: object) -> None:
        return None


async def test_sweep_claims_only_states_a_dead_worker_can_orphan() -> None:
    conn = RecordingConn()
    await queries.sweep_zombie_repos(conn, 1200)  # type: ignore[arg-type]

    sql, args = conn.calls[-1]
    statuses = set(args[0])
    assert statuses == {"cloning", "parsing", "linking", "embedding"}
    # `queued` belongs to Redis, which redelivers; `ready`/`failed` are terminal.
    assert not statuses & {"queued", "ready", "failed"}
    assert args[1] == 1200.0
    assert "worker died" in sql


async def test_sweep_threshold_stays_above_the_job_timeout() -> None:
    """A slow-but-live job must never be swept out from under itself (§10)."""
    from app.config import ZOMBIE_AFTER_S

    assert ZOMBIE_AFTER_S > worker.JOB_TIMEOUT_S


async def test_ingest_repo_records_failure_on_the_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed clone must land in `repos.error`, not only in the worker log."""
    conn = RecordingConn()
    repo_id = uuid.uuid4()

    async def boom(*_a: object, **_k: object) -> None:
        raise CloneError("failed to clone: repository not found")

    monkeypatch.setattr(worker, "run_ingest", boom)
    result = await worker.ingest_repo({"pool": FakePool(conn)}, str(repo_id))

    assert "failed" in result and "CloneError" in result
    sql, args = conn.calls[-1]
    assert "status = 'failed'" in sql
    assert args[0] == repo_id
    assert "repository not found" in args[1]


async def test_ingest_repo_lets_cancellation_propagate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Timeout/abort must stay retryable rather than being recorded as failed."""
    import asyncio

    conn = RecordingConn()

    async def cancelled(*_a: object, **_k: object) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(worker, "run_ingest", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await worker.ingest_repo({"pool": FakePool(conn)}, str(uuid.uuid4()))

    # The claim is expected — it happens before any work (SPEC §15.4). What must
    # NOT happen is a failure being recorded: a cancelled job is retryable, and
    # writing `failed` here would brick a snapshot ARQ intends to run again.
    written = " ".join(sql for sql, _ in conn.calls)
    assert "claimed_by" in written
    assert "failed" not in written


def test_worker_settings_match_the_spec_and_the_budget_rule() -> None:
    assert worker.WorkerSettings.job_timeout == 900  # §10
    assert worker.WorkerSettings.max_tries == 2  # §10
    # ~2s polling keeps a 24/7 worker inside a free managed-Redis command budget
    # (DECISIONS, Upstash note); 0.5s (arq's default) does not.
    assert worker.WorkerSettings.poll_delay >= 2.0
    assert worker.ingest_repo in worker.WorkerSettings.functions
