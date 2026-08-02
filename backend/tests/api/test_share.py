"""§21 shareable answer permalinks.

The feature is small; its security surface is not. `GET /shared/{id}` is the
only route in the API that answers without an identity, so most of what is
pinned here is the boundary: who can publish, what is stored versus what was
claimed, who can retract, and what a stranger with the link can and cannot see.
"""

from __future__ import annotations

import uuid

import httpx

from app.config import SHARED_ANSWER_MAX_CHARS
from tests.api.conftest import (
    FILE_PATH,
    REPO_ID,
    SHARED_ID,
    UNKNOWN_REPO_ID,
    FakeConn,
)

# One client per test, always. The fixtures wire a single global app, so asking
# for `client` and `anon_client` together leaves whichever ran last in charge of
# both. Anything needing a permalink that already exists reads the seeded
# SHARED_ID instead of publishing one first.

BODY = {
    "question": "how does auth work?",
    "answer": "It verifies the token in `pkg/auth.py`.",
    "citations": [{"file_path": FILE_PATH, "start_line": 1, "end_line": 2}],
    "model": "mistral-medium-latest",
}


async def _share(client: httpx.AsyncClient, **over: object) -> httpx.Response:
    return await client.post(f"/repos/{REPO_ID}/share", json={**BODY, **over})


# --- publishing ------------------------------------------------------------


async def test_share_returns_201_and_an_id(client: httpx.AsyncClient) -> None:
    resp = await _share(client)
    assert resp.status_code == 201
    assert resp.json()["id"]


async def test_share_requires_a_session(anon_client: httpx.AsyncClient) -> None:
    resp = await anon_client.post(f"/repos/{REPO_ID}/share", json=BODY)
    assert resp.status_code == 401


async def test_cannot_share_a_repo_you_do_not_own(client: httpx.AsyncClient) -> None:
    resp = await client.post(f"/repos/{UNKNOWN_REPO_ID}/share", json=BODY)
    assert resp.status_code == 404


async def test_an_oversized_answer_is_refused(client: httpx.AsyncClient) -> None:
    """The body is client-supplied; unbounded would be a storage DoS."""
    resp = await _share(client, answer="x" * (SHARED_ANSWER_MAX_CHARS + 1))
    assert resp.status_code == 422


async def test_an_empty_answer_is_refused(client: httpx.AsyncClient) -> None:
    assert (await _share(client, answer="")).status_code == 422


# --- the citations are re-checked, not trusted -----------------------------


async def test_a_fabricated_citation_is_dropped(
    client: httpx.AsyncClient,
) -> None:
    """A client can post any path. Only paths in *this* snapshot survive.

    Without this, a permalink could be made to assert that some file says
    something it does not — the citation is the credibility of the whole
    feature.
    """
    resp = await _share(
        client,
        citations=[
            {"file_path": FILE_PATH, "start_line": 1, "end_line": 2},
            {"file_path": "not/in/repo.py", "start_line": 1, "end_line": 5},
        ],
    )
    share_id = resp.json()["id"]

    body = (await client.get(f"/shared/{share_id}")).json()
    assert [c["file_path"] for c in body["citations"]] == [FILE_PATH]


async def test_an_overrunning_range_is_clamped(client: httpx.AsyncClient) -> None:
    """`pkg/auth.py` is two lines; a citation to line 900 is clamped, not kept."""
    resp = await _share(
        client,
        citations=[{"file_path": FILE_PATH, "start_line": 1, "end_line": 900}],
    )
    body = (await client.get(f"/shared/{resp.json()['id']}")).json()
    assert body["citations"][0]["end_line"] == 2


async def test_too_many_citations_is_refused(client: httpx.AsyncClient) -> None:
    many = [{"file_path": FILE_PATH, "start_line": 1, "end_line": 2}] * 200
    assert (await _share(client, citations=many)).status_code == 422


# --- reading ---------------------------------------------------------------


async def test_a_stranger_can_read_the_permalink(
    anon_client: httpx.AsyncClient,
) -> None:
    """The point of the feature: no session, no account, still resolves."""
    resp = await anon_client.get(f"/shared/{SHARED_ID}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["question"] == "how does auth work?"
    assert body["answer"].startswith("It verifies the token")


async def test_the_read_carries_what_citations_need_to_resolve(
    anon_client: httpx.AsyncClient,
) -> None:
    """Repo URL + pinned commit, so a reader off this app can reach GitHub."""
    body = (await anon_client.get(f"/shared/{SHARED_ID}")).json()
    assert body["repo_url"].startswith("https://github.com/")
    assert body["repo_name"]
    assert body["commit_sha"] == "abc123"


async def test_the_read_does_not_name_the_publisher(
    anon_client: httpx.AsyncClient,
) -> None:
    """Who shared it is the owner's business, not the reader's."""
    body = (await anon_client.get(f"/shared/{SHARED_ID}")).json()
    assert "created_by" not in body


async def test_the_read_carries_no_code_body(
    anon_client: httpx.AsyncClient,
) -> None:
    """§18.4's rule: /files is the only endpoint that serves code."""
    text = (await anon_client.get(f"/shared/{SHARED_ID}")).text
    assert "SECRET_SENTINEL_VALUE" not in text


async def test_an_unknown_share_id_is_404(anon_client: httpx.AsyncClient) -> None:
    unknown = "99999999-9999-9999-9999-999999999999"
    assert (await anon_client.get(f"/shared/{unknown}")).status_code == 404


# --- retracting ------------------------------------------------------------


async def test_the_publisher_can_unshare(client: httpx.AsyncClient) -> None:
    assert (await client.delete(f"/shared/{SHARED_ID}")).status_code == 204
    # And the link stops working — for its publisher and so for everyone.
    assert (await client.get(f"/shared/{SHARED_ID}")).status_code == 404


async def test_a_stranger_cannot_unshare(anon_client: httpx.AsyncClient) -> None:
    """Reading needs no session; retracting does."""
    assert (await anon_client.delete(f"/shared/{SHARED_ID}")).status_code == 401
    assert (await anon_client.get(f"/shared/{SHARED_ID}")).status_code == 200


async def test_unsharing_something_that_is_not_yours_is_404(
    other_client: httpx.AsyncClient,
) -> None:
    """Same answer as "does not exist", so this cannot probe which ids are real."""
    assert (await other_client.delete(f"/shared/{SHARED_ID}")).status_code == 404
    # ...and it is still published, because the wrong user's delete did nothing.
    assert (await other_client.get(f"/shared/{SHARED_ID}")).status_code == 200


async def test_retracting_twice_is_404_the_second_time(
    client: httpx.AsyncClient,
) -> None:
    assert (await client.delete(f"/shared/{SHARED_ID}")).status_code == 204
    assert (await client.delete(f"/shared/{SHARED_ID}")).status_code == 404


async def test_only_validated_citations_reach_storage(
    client: httpx.AsyncClient, conn: FakeConn
) -> None:
    """Belt and braces on the drop: the *stored* row must not carry the fake.

    Looked up by the returned id, not by "the first share" — a seeded row
    exists, and picking that one would make this pass without asserting
    anything about what was just written.
    """
    resp = await _share(
        client,
        citations=[{"file_path": "not/in/repo.py", "start_line": 1, "end_line": 5}],
    )
    stored = conn.shares[uuid.UUID(resp.json()["id"])]
    assert "not/in/repo.py" not in stored["citations"]
    assert stored["citations"] == "[]"
