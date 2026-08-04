"""§8 routes: submit, poll, list, read a file, and every documented error path."""

from __future__ import annotations

import httpx
import pytest

from app.api import deps
from app.main import app
from tests.api.fakes import (
    FAILED_REPO_ID,
    FILE_PATH,
    INDEXING_REPO_ID,
    REPO_ID,
    UNKNOWN_REPO_ID,
    FakeArq,
    FakeConn,
)


async def test_post_repos_creates_row_and_enqueues(
    client: httpx.AsyncClient, arq: FakeArq
) -> None:
    resp = await client.post("/repos", json={"url": "github.com/encode/starlette"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["url"] == "https://github.com/encode/starlette"  # normalized
    assert body["name"] == "encode/starlette"
    assert body["status"] == "queued"
    assert body["progress"] == {
        "files_total": 0,
        "files_parsed": 0,
        "chunks_total": 0,
        "chunks_embedded": 0,
    }
    # The handler must not have done any work itself (hard rule 1) — just queued.
    # `None` is the rev: an ordinary submit takes the branch tip (§28.3).
    assert arq.jobs == [("ingest_repo", (str(body["id"]), None))]


async def test_post_repos_known_url_returns_the_ready_snapshot_and_does_no_work(
    client: httpx.AsyncClient, arq: FakeArq
) -> None:
    """A repeat submit of a `ready` repo returns it — it does NOT re-ingest.

    This is the §14.5 change, and it is the point of the phase. The old
    behaviour re-queued, and the ingest cleared the corpus in place at its
    start — so re-submitting a URL destroyed a corpus somebody else might have
    been mid-chat on. A ready snapshot is frozen; there is nothing to redo.
    """
    resp = await client.post("/repos", json={"url": "https://github.com/owner/ready"})
    assert resp.status_code == 200
    assert resp.json()["id"] == str(REPO_ID)
    assert arq.jobs == []  # the whole point: no work enqueued


async def test_post_repos_failed_repo_is_re_enqueued(
    client: httpx.AsyncClient, arq: FakeArq
) -> None:
    """A `failed` repo is not bricked: re-submitting its URL retries the ingest.

    This is the UI's Retry button (Phase 5) — same endpoint, no special route.
    Post-§14.3 the retry is a **new snapshot** rather than a reset of the failed
    row: a snapshot is written once, and resetting one in place is the mutation
    this phase removed. So the response carries a fresh id, 201, and the stale
    failure is gone because the row carrying it is not the row being returned.
    """
    resp = await client.post("/repos", json={"url": "https://github.com/owner/failed"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] != str(FAILED_REPO_ID)  # a new snapshot, not the old one
    assert body["status"] == "queued"
    assert body["error"] is None  # the stale failure must not linger in the UI
    assert arq.jobs == [("ingest_repo", (body["id"], None))]


async def test_post_repos_does_not_re_enqueue_work_in_flight(
    client: httpx.AsyncClient, arq: FakeArq
) -> None:
    """Two workers must never run delete-and-replace on the same repo."""
    resp = await client.post(
        "/repos", json={"url": "https://github.com/owner/indexing"}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "embedding"
    assert arq.jobs == []


@pytest.mark.parametrize(
    "url", ["", "https://gitlab.com/owner/repo", "https://github.com/owner"]
)
async def test_post_repos_rejects_bad_url(client: httpx.AsyncClient, url: str) -> None:
    resp = await client.post("/repos", json={"url": url})
    assert resp.status_code == 422


async def test_post_repos_503_when_queue_is_down(client: httpx.AsyncClient) -> None:
    """No Redis means no ingest — never an inline clone in the handler."""
    app.dependency_overrides.pop(deps.get_arq)
    app.state.arq = None
    resp = await client.post("/repos", json={"url": "github.com/encode/httpx"})
    assert resp.status_code == 503


async def test_get_repo_returns_progress(client: httpx.AsyncClient) -> None:
    resp = await client.get(f"/repos/{REPO_ID}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["progress"]["chunks_embedded"] == 9
    assert body["head_sha"] == "abc123"


async def test_get_repo_unknown_is_404(client: httpx.AsyncClient) -> None:
    resp = await client.get(f"/repos/{UNKNOWN_REPO_ID}")
    assert resp.status_code == 404
    assert "detail" in resp.json()


async def test_list_repos(client: httpx.AsyncClient) -> None:
    resp = await client.get("/repos")
    assert resp.status_code == 200
    ids = {r["id"] for r in resp.json()["repos"]}
    assert {str(REPO_ID), str(INDEXING_REPO_ID)} <= ids


async def test_get_file_serves_stored_content(
    client: httpx.AsyncClient, conn: FakeConn
) -> None:
    resp = await client.get(f"/repos/{REPO_ID}/files", params={"path": FILE_PATH})
    assert resp.status_code == 200
    body = resp.json()
    assert body["path"] == FILE_PATH
    assert body["content"] == conn.files[FILE_PATH]["content"]
    assert body["n_lines"] == 2


async def test_get_file_unknown_path_is_404(client: httpx.AsyncClient) -> None:
    resp = await client.get(f"/repos/{REPO_ID}/files", params={"path": "nope.py"})
    assert resp.status_code == 404


async def test_get_file_unknown_repo_is_404(client: httpx.AsyncClient) -> None:
    resp = await client.get(
        f"/repos/{UNKNOWN_REPO_ID}/files", params={"path": FILE_PATH}
    )
    assert resp.status_code == 404


async def test_chat_before_ready_is_409_with_status(
    client: httpx.AsyncClient,
) -> None:
    resp = await client.post(
        f"/repos/{INDEXING_REPO_ID}/chat", json={"question": "how does auth work?"}
    )
    assert resp.status_code == 409
    body = resp.json()
    assert body["detail"] == "repo not ready"
    assert body["status"] == "embedding"
    # Every error carries the id that finds its server-side log line.
    assert body["request_id"] == resp.headers["x-request-id"]


async def test_chat_unknown_repo_is_404(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        f"/repos/{UNKNOWN_REPO_ID}/chat", json={"question": "anything"}
    )
    assert resp.status_code == 404
