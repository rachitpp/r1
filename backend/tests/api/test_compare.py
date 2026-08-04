"""§28 snapshot comparison: what the index holds now that it did not before.

A *structural* diff. `git diff` answers "which lines changed" and answers it
better; these pin the part only this can answer — files, symbols and packages
appearing or disappearing between two immutable corpora — plus the two refusals,
which exist because a confident wrong answer is worse than saying no.
"""

from __future__ import annotations

import httpx

from tests.api.fakes import (
    NAIVE_REPO_ID,
    OLDER_REPO_ID,
    REPO_ID,
    UNKNOWN_REPO_ID,
    FakeArq,
)


async def test_reads_as_this_snapshot_compared_against_base(
    client: httpx.AsyncClient,
) -> None:
    resp = await client.get(f"/repos/{REPO_ID}/compare?base={OLDER_REPO_ID}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["head"]["id"] == str(REPO_ID)
    assert body["base"]["id"] == str(OLDER_REPO_ID)


async def test_reports_symbols_added_and_removed(client: httpx.AsyncClient) -> None:
    body = (
        await client.get(f"/repos/{REPO_ID}/compare?base={OLDER_REPO_ID}")
    ).json()
    assert [s["qualname"] for s in body["symbols_added"]] == ["pkg.auth.Signer"]
    assert [s["qualname"] for s in body["symbols_removed"]] == [
        "pkg.gone.old_helper"
    ]
    # The kind travels with it: "a class disappeared" and "a method
    # disappeared" are different sizes of news.
    assert body["symbols_removed"][0]["kind"] == "function"


async def test_reports_files_and_dependencies(client: httpx.AsyncClient) -> None:
    body = (
        await client.get(f"/repos/{REPO_ID}/compare?base={OLDER_REPO_ID}")
    ).json()
    assert body["files_removed"] == ["pkg/gone.py"]
    assert body["dependencies_added"] == ["werkzeug"]
    assert body["dependencies_removed"] == ["six"]


async def test_carries_the_commits_between_the_two(
    client: httpx.AsyncClient,
) -> None:
    body = (
        await client.get(f"/repos/{REPO_ID}/compare?base={OLDER_REPO_ID}")
    ).json()
    assert body["commits_indexed"] is True
    assert len(body["commits"]) == 2
    assert body["commits"][0]["subject"]


# --- the two refusals ------------------------------------------------------


async def test_comparing_different_repos_is_rejected(
    client: httpx.AsyncClient, conn
) -> None:
    """A cross-repo diff is every symbol in one repo against every symbol in
    another — a number that looks like a finding and means nothing."""
    from tests.api.fakes import INDEXING_REPO_ID

    resp = await client.get(f"/repos/{REPO_ID}/compare?base={INDEXING_REPO_ID}")
    assert resp.status_code == 400
    assert "different repositories" in resp.json()["detail"]


async def test_comparing_across_chunking_strategies_is_rejected(
    client: httpx.AsyncClient,
) -> None:
    """`naive` stores no symbols (§2.7), so this would report the whole repo
    as deleted — an artefact of the question, not a fact about the code."""
    resp = await client.get(f"/repos/{REPO_ID}/compare?base={NAIVE_REPO_ID}")
    assert resp.status_code == 400
    assert "chunked differently" in resp.json()["detail"]


# --- tenancy (§13.5) -------------------------------------------------------


async def test_unknown_base_is_404(client: httpx.AsyncClient) -> None:
    resp = await client.get(f"/repos/{REPO_ID}/compare?base={UNKNOWN_REPO_ID}")
    assert resp.status_code == 404


async def test_another_tenants_snapshot_is_404_on_either_side(
    other_client: httpx.AsyncClient,
) -> None:
    """Both sides are checked: a comparison is a read of two corpora, and
    checking only the path one would leak the other."""
    assert (
        await other_client.get(f"/repos/{REPO_ID}/compare?base={OLDER_REPO_ID}")
    ).status_code == 404


async def test_anonymous_is_rejected(anon_client: httpx.AsyncClient) -> None:
    resp = await anon_client.get(f"/repos/{REPO_ID}/compare?base={OLDER_REPO_ID}")
    assert resp.status_code == 401


# --- §28.3 getting a second snapshot ---------------------------------------


async def test_siblings_offers_other_snapshots_of_the_same_repo(
    client: httpx.AsyncClient,
) -> None:
    body = (await client.get(f"/repos/{REPO_ID}/snapshots")).json()
    ids = {s["id"] for s in body["siblings"]}
    assert str(OLDER_REPO_ID) in ids
    # Itself is not a comparison base.
    assert str(REPO_ID) not in ids


async def test_siblings_excludes_a_different_chunking_strategy(
    client: httpx.AsyncClient,
) -> None:
    """Offering it would only produce a 400 on click (§28.1)."""
    body = (await client.get(f"/repos/{REPO_ID}/snapshots")).json()
    assert str(NAIVE_REPO_ID) not in {s["id"] for s in body["siblings"]}


async def test_siblings_is_scoped_to_the_caller(
    other_client: httpx.AsyncClient,
) -> None:
    assert (
        await other_client.get(f"/repos/{REPO_ID}/snapshots")
    ).status_code == 404


async def test_submitting_with_a_rev_makes_a_new_snapshot(
    client: httpx.AsyncClient, arq: FakeArq
) -> None:
    """A pinned rev must not be answered with "here is the newest one".

    The caller asked for a *particular* commit, and the newest snapshot is by
    definition not it (§28.3).
    """
    resp = await client.post(
        "/repos", json={"url": "https://github.com/owner/ready", "rev": "abc1234"}
    )
    assert resp.status_code == 201
    assert resp.json()["id"] != str(REPO_ID)

    # The rev rides along to the worker, which resolves and dedups it after the
    # clone — here is where it would silently be dropped.
    name, args = arq.jobs[-1]
    assert name == "ingest_repo"
    assert args[1] == "abc1234"


async def test_an_ordinary_submit_still_enqueues_without_a_rev(
    client: httpx.AsyncClient, arq: FakeArq
) -> None:
    await client.post("/repos", json={"url": "https://github.com/owner/fresh"})
    name, args = arq.jobs[-1]
    assert name == "ingest_repo"
    assert args[1] is None


async def test_submitting_without_a_rev_still_returns_the_existing_snapshot(
    client: httpx.AsyncClient,
) -> None:
    """§14.5 is unchanged for the common path."""
    resp = await client.post("/repos", json={"url": "https://github.com/owner/ready"})
    assert resp.status_code == 200
    assert resp.json()["id"] == str(REPO_ID)
