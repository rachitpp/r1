"""`GET /repos/{id}/checklist` (§22.2).

The ordering and the degradation are pinned in `tests/test_checklist.py`
against the pure builder. What is left for the route is the boundary and the
one claim that justifies the whole design: **this endpoint spends nothing.**
"""

from __future__ import annotations

import httpx

from tests.api.fakes import REPO_ID, UNKNOWN_REPO_ID, FakeArq, FakeConn


async def test_checklist_returns_steps_in_reading_order(
    client: httpx.AsyncClient,
) -> None:
    resp = await client.get(f"/repos/{REPO_ID}/checklist")
    assert resp.status_code == 200
    kinds = [i["kind"] for i in resp.json()["items"]]
    assert kinds == [
        "entry_point",
        "hub",
        "key_symbol",
        "public_api",
        "most_tested",
    ]


async def test_every_step_is_openable_and_askable(
    client: httpx.AsyncClient,
) -> None:
    """A step needs somewhere to look and something to ask, or it is a slogan."""
    items = (await client.get(f"/repos/{REPO_ID}/checklist")).json()["items"]
    for item in items:
        assert item["file_path"]
        assert item["start_line"] >= 1
        assert item["end_line"] >= item["start_line"]
        assert item["question"].endswith("?")
        assert item["title"] and item["detail"]


async def test_the_checklist_costs_no_model_call(
    client: httpx.AsyncClient, arq: FakeArq
) -> None:
    """The design claim, asserted rather than trusted.

    §19's overview enqueues a job that spends one request from a 20/day tier.
    This endpoint answers from the graph, so nothing may reach the queue — if a
    future change routes it through the model, this fails.
    """
    await client.get(f"/repos/{REPO_ID}/checklist")
    assert arq.jobs == []


async def test_the_checklist_carries_no_code_body(
    client: httpx.AsyncClient,
) -> None:
    """§18.4's rule: /files is the only endpoint that serves code."""
    text = (await client.get(f"/repos/{REPO_ID}/checklist")).text
    assert "SECRET_SENTINEL_VALUE" not in text


async def test_checklist_404s_for_an_unowned_repo(
    client: httpx.AsyncClient,
) -> None:
    assert (
        await client.get(f"/repos/{UNKNOWN_REPO_ID}/checklist")
    ).status_code == 404


async def test_checklist_401s_without_a_session(
    anon_client: httpx.AsyncClient,
) -> None:
    assert (await anon_client.get(f"/repos/{REPO_ID}/checklist")).status_code == 401


async def test_ranges_are_clamped_to_the_real_file_length(
    client: httpx.AsyncClient, conn: FakeConn
) -> None:
    """`pkg/auth.py` is two lines; no step may cite past its end."""
    items = (await client.get(f"/repos/{REPO_ID}/checklist")).json()["items"]
    for item in items:
        if item["file_path"] == "pkg/auth.py":
            assert item["end_line"] <= 2
