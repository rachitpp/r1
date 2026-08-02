"""The §20 history view.

Like §18, this is a read over rows ingest already wrote — no model, no tool
budget. The tests pin the three things that are query-time decisions rather
than storage: ordering, merge exclusion, and path scoping. Plus the one thing
this endpoint has that §18.3 does not — the `indexed` flag that keeps an empty
list from lying.
"""

from __future__ import annotations

import httpx

from app.config import HISTORY_PAGE_MAX
from tests.api.conftest import (
    FILE_PATH,
    INDEXING_REPO_ID,
    REPO_ID,
    UNKNOWN_REPO_ID,
    FakeConn,
)


async def test_history_is_reverse_chronological(client: httpx.AsyncClient) -> None:
    resp = await client.get(f"/repos/{REPO_ID}/history")
    assert resp.status_code == 200
    body = resp.json()

    # Newest first. A history in insertion order is a list, not a history.
    assert [c["sha"] for c in body["commits"]] == ["d0cs004", "c0ffee1", "beef002"]
    assert body["path"] is None
    assert body["indexed"] is True
    assert body["truncated"] is False


async def test_merges_are_excluded_by_default(client: httpx.AsyncClient) -> None:
    body = (await client.get(f"/repos/{REPO_ID}/history")).json()
    assert "merge003" not in [c["sha"] for c in body["commits"]]
    assert body["include_merges"] is False


async def test_merges_are_reachable_with_the_flag(client: httpx.AsyncClient) -> None:
    """Flag-and-filter (§2.6): stored at ingest, decided here."""
    body = (
        await client.get(f"/repos/{REPO_ID}/history", params={"include_merges": True})
    ).json()
    shas = [c["sha"] for c in body["commits"]]
    assert "merge003" in shas
    assert body["include_merges"] is True
    # Still ordered, with the merge landing in its chronological place.
    assert shas == ["d0cs004", "c0ffee1", "merge003", "beef002"]


async def test_path_scopes_to_one_file(client: httpx.AsyncClient) -> None:
    body = (
        await client.get(f"/repos/{REPO_ID}/history", params={"path": FILE_PATH})
    ).json()
    assert [c["sha"] for c in body["commits"]] == ["c0ffee1", "beef002"]
    assert body["path"] == FILE_PATH
    # The commit that touched only README is gone, not merely reordered.
    assert "d0cs004" not in [c["sha"] for c in body["commits"]]


async def test_commit_carries_author_and_line_deltas(
    client: httpx.AsyncClient,
) -> None:
    body = (
        await client.get(f"/repos/{REPO_ID}/history", params={"path": FILE_PATH})
    ).json()
    newest = body["commits"][0]
    assert newest["author_name"] == "Ada"
    assert newest["author_email"] == "ada@example.com"
    assert newest["subject"] == "auth: reject expired tokens"
    assert newest["body"] == "The check was there and never ran."
    assert (newest["insertions"], newest["deletions"]) == (12, 3)
    assert newest["is_merge"] is False


async def test_missing_author_email_is_null_not_empty(
    client: httpx.AsyncClient,
) -> None:
    """`git log` yields an empty author email often enough to matter (§20.1)."""
    body = (
        await client.get(f"/repos/{REPO_ID}/history", params={"path": FILE_PATH})
    ).json()
    oldest = body["commits"][-1]
    assert oldest["author_email"] is None
    assert oldest["body"] is None


async def test_unknown_path_is_empty_not_404(client: httpx.AsyncClient) -> None:
    """Same existence-oracle reasoning as §18.3."""
    resp = await client.get(
        f"/repos/{REPO_ID}/history", params={"path": "does/not/exist.py"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["commits"] == []
    # ...but history *was* indexed for this snapshot, and the flag says so.
    assert body["indexed"] is True


async def test_unindexed_snapshot_reports_not_indexed(
    client: httpx.AsyncClient,
) -> None:
    """The distinction the `indexed` field exists for.

    Every snapshot ingested before §20 has zero commit rows. Without this flag
    that is indistinguishable from a repo with no history, and the UI would
    confidently say "no commits" about a corpus nobody ever walked.
    """
    body = (await client.get(f"/repos/{INDEXING_REPO_ID}/history")).json()
    assert body["commits"] == []
    assert body["indexed"] is False


async def test_limit_is_capped_at_the_page_max(client: httpx.AsyncClient) -> None:
    over = await client.get(
        f"/repos/{REPO_ID}/history", params={"limit": HISTORY_PAGE_MAX + 1}
    )
    assert over.status_code == 422
    assert (await client.get(f"/repos/{REPO_ID}/history", params={"limit": 0})).status_code == 422


async def test_limit_reaches_sql_and_sets_truncated(
    client: httpx.AsyncClient,
) -> None:
    body = (await client.get(f"/repos/{REPO_ID}/history", params={"limit": 2})).json()
    assert len(body["commits"]) == 2
    assert body["truncated"] is True


async def test_history_404s_for_an_unowned_repo(client: httpx.AsyncClient) -> None:
    resp = await client.get(f"/repos/{UNKNOWN_REPO_ID}/history")
    assert resp.status_code == 404


async def test_history_401s_without_a_session(
    anon_client: httpx.AsyncClient,
) -> None:
    resp = await anon_client.get(f"/repos/{REPO_ID}/history")
    assert resp.status_code == 401


async def test_history_returns_no_code_body(
    client: httpx.AsyncClient, conn: FakeConn
) -> None:
    """§18.4's rule holds here too: /files is the only endpoint serving code."""
    text = (await client.get(f"/repos/{REPO_ID}/history")).text
    assert "SECRET_SENTINEL_VALUE" not in text
