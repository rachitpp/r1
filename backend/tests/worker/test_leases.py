"""Job leases (SPEC §15) against a live database.

These need real SQL: every property here is a race or a time window, and a fake
connection that answers by substring can only confirm the statement was sent.

Read §15.1 before changing any of this. The premise V2.md gave for leases —
"worker 2's startup sweeps worker 1's live job" — was **wrong**: the old sweep
was already time-based. What leases actually add is an *unconditional* liveness
signal, so a silent-but-healthy phase is no longer indistinguishable from a dead
worker, plus the identity and the one-in-flight guarantee below.
"""

from __future__ import annotations

import asyncio
import uuid

import asyncpg
import pytest

from app.config import get_settings
from app.db import queries
from app.db.pool import close_pool, create_pool

pytestmark = pytest.mark.integration


async def _db_reachable() -> bool:
    try:
        conn = await asyncpg.connect(get_settings().DATABASE_URL, timeout=5)
    except Exception:
        return False
    await conn.close()
    return True


@pytest.fixture
def require_db() -> None:
    if not asyncio.run(_db_reachable()):
        pytest.skip("database unreachable")


@pytest.fixture
async def source(require_db: None):
    """A throwaway source, deleted with everything under it in teardown."""
    pool = await create_pool(get_settings().DATABASE_URL)
    url = f"file:///leases-test/{uuid.uuid4()}"
    try:
        async with pool.acquire() as conn:
            source_id = await queries.get_or_create_source(
                conn, url=url, name="test/leases"
            )
            yield pool, source_id
    finally:
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM repo_sources WHERE url = $1", url)
        await close_pool(pool)


async def test_only_one_worker_can_claim_a_snapshot(source) -> None:
    """The claim is the race resolver (§15.4).

    Two workers polling the same job is normal — ARQ redelivers, and a retry can
    overlap a slow original. Exactly one UPDATE may match, or both would ingest
    into the same snapshot and double every row.
    """
    pool, source_id = source
    async with pool.acquire() as conn:
        snap = await queries.create_snapshot(conn, source_id)
        first = await queries.claim_snapshot(conn, snap, "worker-a:1")
        second = await queries.claim_snapshot(conn, snap, "worker-b:2")
    assert first is True
    assert second is False

    # The claim moved the row out of `queued` — that transition IS the exclusion
    # (§15.4). Leaving it queued is what made an earlier version of this claim
    # vacuous, and this test is what caught it.
    async with pool.acquire() as conn:
        row = await queries.get_repo(conn, snap)
    assert row is not None
    assert row["status"] == "cloning"


async def test_a_source_cannot_have_two_in_flight_snapshots(source) -> None:
    """§15.3's partial unique index, enforced by Postgres rather than by hope."""
    pool, source_id = source
    async with pool.acquire() as conn:
        await queries.create_snapshot(conn, source_id)
        with pytest.raises(asyncpg.UniqueViolationError):
            await queries.create_snapshot(conn, source_id)


async def test_a_finished_snapshot_frees_the_source_for_another(source) -> None:
    """The index is partial on purpose: a source accumulates ready snapshots.

    If it constrained finished rows too, a repo could be indexed exactly once
    ever — the opposite of §14, where a new commit is a new snapshot.
    """
    pool, source_id = source
    async with pool.acquire() as conn:
        first = await queries.create_snapshot(conn, source_id)
        await queries.set_repo_status(conn, first, "ready")
        second = await queries.create_snapshot(conn, source_id)
        assert second != first


async def test_a_different_strategy_is_not_blocked(source) -> None:
    """The key includes `strategy`, so an AST and a naive ingest can overlap."""
    pool, source_id = source
    async with pool.acquire() as conn:
        await queries.create_snapshot(conn, source_id, strategy="ast")
        naive = await queries.create_snapshot(conn, source_id, strategy="naive")
        assert naive is not None


async def test_the_sweep_reclaims_an_expired_lease_and_spares_a_fresh_one(
    source,
) -> None:
    """The whole point of a heartbeat: liveness that does not depend on progress.

    Two claimed snapshots, one still beating and one not. Only the silent one may
    be reclaimed — a sweep that took both would kill live work, which is the
    failure mode the tight ``LEASE_EXPIRY_S`` window makes possible.

    They use different strategies because §15.3 permits only one in-flight
    snapshot per (source, strategy), and this test needs two at once.
    """
    pool, source_id = source
    async with pool.acquire() as conn:
        stale = await queries.create_snapshot(conn, source_id, strategy="ast")
        await queries.claim_snapshot(conn, stale, "dead-worker:1")
        await queries.set_repo_status(conn, stale, "embedding")

        fresh = await queries.create_snapshot(conn, source_id, strategy="naive")
        await queries.claim_snapshot(conn, fresh, "live-worker:2")
        await queries.set_repo_status(conn, fresh, "embedding")

        # Age both leases past the window, then beat only the live one — exactly
        # what a running worker's timer does and a dead one cannot.
        await asyncio.sleep(1.1)
        await queries.touch_heartbeat(conn, fresh)

        swept = await queries.sweep_expired_leases(conn, 1)

        assert str(stale) in swept
        assert str(fresh) not in swept

        dead = await queries.get_repo(conn, stale)
        assert dead is not None
        assert dead["status"] == "failed"
        assert dead["error"] == "worker lease expired"

        alive = await queries.get_repo(conn, fresh)
        assert alive is not None
        assert alive["status"] == "embedding"  # untouched


async def test_the_sweep_ignores_rows_that_never_had_a_lease(source) -> None:
    """Leaseless rows belong to the old timer, not to this sweep (§15.4).

    Both paths run at worker startup; if either reaped the other's rows the two
    windows would fight, and a snapshot from before the lease columns would be
    failed 120 seconds into a legitimate twenty-minute ingest.
    """
    pool, source_id = source
    async with pool.acquire() as conn:
        leaseless = await queries.create_snapshot(conn, source_id)
        await queries.set_repo_status(conn, leaseless, "embedding")
        swept = await queries.sweep_expired_leases(conn, 0)
        assert str(leaseless) not in swept

        row = await queries.get_repo(conn, leaseless)
        assert row is not None
        assert row["status"] == "embedding"


async def test_per_user_quota_counts_only_that_user(source) -> None:
    """§15.5. One user's queue must not refuse another's first submission."""
    pool, source_id = source
    async with pool.acquire() as conn:
        mine = await queries.upsert_user(
            conn, github_id=-101, login="quota-a", name=None, avatar_url=None
        )
        theirs = await queries.upsert_user(
            conn, github_id=-102, login="quota-b", name=None, avatar_url=None
        )
        try:
            snap = await queries.create_snapshot(conn, source_id)
            await queries.link_user_repo(conn, mine["id"], snap)

            assert await queries.count_active_ingests_for_user(conn, mine["id"]) == 1
            assert await queries.count_active_ingests_for_user(conn, theirs["id"]) == 0
        finally:
            await conn.execute(
                "DELETE FROM users WHERE github_id IN (-101, -102)"
            )


async def test_a_failed_snapshot_does_not_block_a_retry_at_the_same_commit(
    source,
) -> None:
    """A corpse must not brick a commit (migration 010).

    007 made (source, commit_sha, strategy) unconditionally unique, and the V3
    three-worker run found what that costs: a worker killed mid-ingest leaves a
    `failed` snapshot that KEEPS its commit_sha, so every retry cloned the same
    commit and died on the unique key. One worker death made that commit
    permanently un-ingestable, and the error text pointed nowhere near the cause.

    The constraint means "one stored corpus per repo/commit/strategy". A failed
    snapshot is not a corpus — it is a partial write nobody can read — so the
    index is partial on `status = 'ready'`.
    """
    pool, source_id = source
    sha = "deadbeef" * 5
    async with pool.acquire() as conn:
        dead = await queries.create_snapshot(conn, source_id)
        await queries.set_repo_clone_info(
            conn, dead, name="test/leases", head_sha=sha, default_branch="main"
        )
        await queries.fail_repo(conn, dead, "worker lease expired")

        # The retry: same source, same commit, same strategy.
        retry = await queries.create_snapshot(conn, source_id)
        await queries.set_repo_clone_info(
            conn, retry, name="test/leases", head_sha=sha, default_branch="main"
        )
        await queries.set_repo_status(conn, retry, "ready")

        row = await queries.get_repo(conn, retry)
        assert row is not None
        assert row["status"] == "ready"
        assert row["head_sha"] == sha


async def test_two_ready_snapshots_of_one_commit_are_still_refused(source) -> None:
    """The narrowed index must still do its actual job (§14.2).

    Making it partial on `ready` is only safe if `ready` remains exclusive —
    otherwise one source could hold two stored corpora of the same commit and
    every dedup lookup would pick arbitrarily between them.
    """
    pool, source_id = source
    sha = "cafebabe" * 5
    async with pool.acquire() as conn:
        first = await queries.create_snapshot(conn, source_id)
        await queries.set_repo_clone_info(
            conn, first, name="test/leases", head_sha=sha, default_branch="main"
        )
        await queries.set_repo_status(conn, first, "ready")

        second = await queries.create_snapshot(conn, source_id)
        await queries.set_repo_clone_info(
            conn, second, name="test/leases", head_sha=sha, default_branch="main"
        )
        with pytest.raises(asyncpg.UniqueViolationError):
            await queries.set_repo_status(conn, second, "ready")
