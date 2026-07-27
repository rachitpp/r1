"""§8 routes: submit, poll, list, read a file, and every documented error path."""

from __future__ import annotations

import httpx
import pytest

from app.api import deps
from app.main import app
from tests.api.conftest import (
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
    assert arq.jobs == [("ingest_repo", (str(body["id"]),))]


async def test_post_repos_known_url_returns_200_and_re_enqueues(
    client: httpx.AsyncClient, arq: FakeArq
) -> None:
    """A repeat submit of a `ready` repo is a re-ingest request, not an error."""
    resp = await client.post("/repos", json={"url": "https://github.com/owner/ready"})
    assert resp.status_code == 200
    assert resp.json()["id"] == str(REPO_ID)
    assert arq.jobs == [("ingest_repo", (str(REPO_ID),))]


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
    assert resp.json() == {"detail": "repo not ready", "status": "embedding"}


async def test_chat_unknown_repo_is_404(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        f"/repos/{UNKNOWN_REPO_ID}/chat", json={"question": "anything"}
    )
    assert resp.status_code == 404
