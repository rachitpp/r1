"""§24 call-hierarchy trace.

A bounded transitive walk returning *pointers*, not code. The properties worth
pinning are the ones that make it safe to run on a real graph: the depth bound
reaching SQL, truncation keeping near neighbours, and the boundary.
"""

from __future__ import annotations

import httpx

from app.config import TRACE_MAX_DEPTH
from tests.api.conftest import FILE_PATH, REPO_ID, UNKNOWN_REPO_ID


async def test_trace_returns_the_root_and_its_reachable_nodes(
    client: httpx.AsyncClient,
) -> None:
    resp = await client.get(
        f"/repos/{REPO_ID}/trace", params={"symbol": "verify_token"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["root"]["qualname"] == "pkg.auth.verify_token"
    assert body["root"]["file_path"] == FILE_PATH
    assert [n["qualname"] for n in body["nodes"]] == [
        "pkg.auth.Signer",
        "pkg.util.b64decode",
    ]


async def test_each_node_carries_the_hop_it_came_through(
    client: httpx.AsyncClient,
) -> None:
    """`via` + `depth` is the path, without serialising a chain per node."""
    body = (
        await client.get(f"/repos/{REPO_ID}/trace", params={"symbol": "verify_token"})
    ).json()
    deep = body["nodes"][1]
    assert deep["depth"] == 2
    assert deep["via"] == "pkg.auth.Signer"


async def test_nodes_are_ordered_nearest_first(client: httpx.AsyncClient) -> None:
    """Truncation must keep the near neighbours: hop one is the answer."""
    body = (
        await client.get(f"/repos/{REPO_ID}/trace", params={"symbol": "verify_token"})
    ).json()
    depths = [n["depth"] for n in body["nodes"]]
    assert depths == sorted(depths)


async def test_depth_reaches_sql_rather_than_being_filtered_after(
    client: httpx.AsyncClient,
) -> None:
    """A fake that ignored the bound would pass a shallower assertion for free."""
    body = (
        await client.get(
            f"/repos/{REPO_ID}/trace", params={"symbol": "verify_token", "depth": 1}
        )
    ).json()
    assert [n["depth"] for n in body["nodes"]] == [1]
    assert body["max_depth"] == 1


async def test_depth_is_capped(client: httpx.AsyncClient) -> None:
    """The explosion guard FEATURE-IDEAS 2.3 asked for, refused at the edge."""
    over = await client.get(
        f"/repos/{REPO_ID}/trace",
        params={"symbol": "verify_token", "depth": TRACE_MAX_DEPTH + 1},
    )
    assert over.status_code == 422
    assert (
        await client.get(
            f"/repos/{REPO_ID}/trace", params={"symbol": "verify_token", "depth": 0}
        )
    ).status_code == 422


async def test_direction_must_be_in_or_out(client: httpx.AsyncClient) -> None:
    resp = await client.get(
        f"/repos/{REPO_ID}/trace",
        params={"symbol": "verify_token", "direction": "sideways"},
    )
    assert resp.status_code == 422


async def test_both_directions_are_accepted(client: httpx.AsyncClient) -> None:
    for direction in ("in", "out"):
        resp = await client.get(
            f"/repos/{REPO_ID}/trace",
            params={"symbol": "verify_token", "direction": direction},
        )
        assert resp.status_code == 200
        assert resp.json()["direction"] == direction


async def test_an_unknown_symbol_is_404(client: httpx.AsyncClient) -> None:
    resp = await client.get(
        f"/repos/{REPO_ID}/trace", params={"symbol": "no_such_thing"}
    )
    assert resp.status_code == 404


async def test_trace_carries_no_code_body(client: httpx.AsyncClient) -> None:
    """The whole point: pointers, not bodies. /files serves code (§18.4)."""
    text = (
        await client.get(f"/repos/{REPO_ID}/trace", params={"symbol": "verify_token"})
    ).text
    assert "SECRET_SENTINEL_VALUE" not in text


async def test_trace_404s_for_an_unowned_repo(client: httpx.AsyncClient) -> None:
    resp = await client.get(
        f"/repos/{UNKNOWN_REPO_ID}/trace", params={"symbol": "verify_token"}
    )
    assert resp.status_code == 404


async def test_trace_401s_without_a_session(anon_client: httpx.AsyncClient) -> None:
    resp = await anon_client.get(
        f"/repos/{REPO_ID}/trace", params={"symbol": "verify_token"}
    )
    assert resp.status_code == 401


async def test_symbol_is_required(client: httpx.AsyncClient) -> None:
    assert (await client.get(f"/repos/{REPO_ID}/trace")).status_code == 422
