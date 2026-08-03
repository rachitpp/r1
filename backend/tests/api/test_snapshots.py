"""Snapshot semantics (SPEC §14) — dedup and immutability at the route level.

The §14.5 table, asserted. These are the behaviours the phase exists to create:
a corpus somebody is reading is never the corpus somebody else is rebuilding,
and a second user asking for a known repo costs nothing.
"""

from __future__ import annotations

import httpx

from .conftest import FAILED_REPO_ID, REPO_ID, FakeArq, FakeConn

READY_URL = "https://github.com/owner/ready"
FAILED_URL = "https://github.com/owner/failed"
INDEXING_URL = "https://github.com/owner/indexing"


async def test_two_users_submitting_one_repo_produce_one_job_and_two_libraries(
    client: httpx.AsyncClient,
    other_client: httpx.AsyncClient,
    arq: FakeArq,
    conn: FakeConn,
) -> None:
    """The saving, at the route level (§14.4/§14.5).

    Both users end up holding the *same* snapshot id — one corpus, two library
    rows — and the second submission enqueues nothing. At a thousand users on a
    popular repo this is the difference between one ingest and a thousand.
    """
    first = await client.post("/repos", json={"url": READY_URL})
    second = await other_client.post("/repos", json={"url": READY_URL})

    assert first.json()["id"] == second.json()["id"] == str(REPO_ID)
    assert arq.jobs == []  # a ready snapshot needs no work from either caller

    owners = {u for (u, s) in conn.user_repos if s == REPO_ID}
    assert len(owners) == 2  # two libraries, one corpus


async def test_a_ready_snapshot_is_never_re_ingested(
    client: httpx.AsyncClient, arq: FakeArq
) -> None:
    """The race this phase removes, stated as an invariant.

    Before §14, this POST re-queued and the ingest called `clear_repo_content`
    at its start — deleting the corpus out from under anyone mid-chat. Now the
    ready snapshot is returned untouched and no job exists to do the deleting.
    """
    for _ in range(3):
        resp = await client.post("/repos", json={"url": READY_URL})
        assert resp.status_code == 200
        assert resp.json()["id"] == str(REPO_ID)
    assert arq.jobs == []


async def test_a_retry_creates_a_new_snapshot_rather_than_resetting_the_failed_one(
    client: httpx.AsyncClient, arq: FakeArq
) -> None:
    """§14.3: a snapshot is written once. Retry supersedes, never rewrites."""
    resp = await client.post("/repos", json={"url": FAILED_URL})
    assert resp.status_code == 201
    new_id = resp.json()["id"]
    assert new_id != str(FAILED_REPO_ID)
    assert arq.jobs == [("ingest_repo", (new_id, None))]


async def test_an_in_flight_snapshot_is_joined_not_duplicated(
    client: httpx.AsyncClient, other_client: httpx.AsyncClient, arq: FakeArq
) -> None:
    """Two workers must never run the same ingest (§14.5, in-flight row)."""
    first = await client.post("/repos", json={"url": INDEXING_URL})
    second = await other_client.post("/repos", json={"url": INDEXING_URL})
    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["status"] == "embedding"
    assert arq.jobs == []


async def test_the_submitter_can_always_see_what_they_submitted(
    client: httpx.AsyncClient, conn: FakeConn
) -> None:
    """A library link is written on every path, ready or fresh.

    Without this a submission could produce a snapshot the submitter cannot
    reach — which is exactly the orphan the CLI used to create, and which reads
    as data loss rather than as a permissions rule.
    """
    resp = await client.post("/repos", json={"url": "https://github.com/psf/requests"})
    assert resp.status_code == 201
    snapshot_id = resp.json()["id"]
    assert (await client.get(f"/repos/{snapshot_id}")).status_code == 200
