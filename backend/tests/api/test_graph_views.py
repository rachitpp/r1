"""The §18 graph views: module rollup and test↔code linkage.

Both endpoints are *reads over the existing symbol graph* — no model, no tool
budget, no ingest. The tests below are written to pin exactly that: what comes
back is a deterministic function of the graph, ownership is enforced by the same
one place everything else is (§13.5), and the §12 caps reach SQL rather than
being applied after the fact.
"""

from __future__ import annotations

import httpx

from app.config import ARCH_MAX_EDGES, ARCH_MAX_NODES, COVERAGE_MAX_LINKS
from tests.api.conftest import (
    FILE_PATH,
    REPO_ID,
    TEST_FILE_PATH,
    UNKNOWN_REPO_ID,
    FakeConn,
)

# --- §18.2 architecture ----------------------------------------------------


async def test_architecture_returns_modules_ranked_by_fan_in(
    client: httpx.AsyncClient,
) -> None:
    resp = await client.get(f"/repos/{REPO_ID}/architecture")
    assert resp.status_code == 200
    body = resp.json()

    # The module every other module depends on comes first. Ranking is the whole
    # point of the view: an unranked module list is `ls`.
    assert [n["path"] for n in body["nodes"]][0] == FILE_PATH
    assert body["nodes"][0]["fan_in"] == 2
    assert body["nodes"][0]["fan_out"] == 0
    assert body["include_tests"] is False
    assert body["truncated"] is False


async def test_architecture_edges_carry_kind_and_weight(
    client: httpx.AsyncClient,
) -> None:
    body = (await client.get(f"/repos/{REPO_ID}/architecture")).json()
    edges = {(e["from_path"], e["to_path"]): e for e in body["edges"]}

    assert edges[("pkg/client.py", FILE_PATH)]["kind"] == "calls"
    # Weight is what lets a renderer draw "deeply coupled" differently from "one
    # import" — a boolean edge list would lose it.
    assert edges[("pkg/client.py", FILE_PATH)]["weight"] == 4
    assert edges[("pkg/api.py", FILE_PATH)]["kind"] == "imports"


async def test_architecture_excludes_tests_by_default(
    client: httpx.AsyncClient,
) -> None:
    """§6.3 flag-and-filter: extracted for every symbol, decided here."""
    default = (await client.get(f"/repos/{REPO_ID}/architecture")).json()
    assert TEST_FILE_PATH not in [n["path"] for n in default["nodes"]]

    with_tests = (
        await client.get(f"/repos/{REPO_ID}/architecture?include_tests=true")
    ).json()
    assert TEST_FILE_PATH in [n["path"] for n in with_tests["nodes"]]
    assert with_tests["include_tests"] is True


async def test_architecture_passes_the_spec_caps_to_sql(
    client: httpx.AsyncClient, conn: FakeConn
) -> None:
    """The caps are a LIMIT, not a slice of an already-materialised graph.

    Truncating in Python would mean Postgres had already built the full rollup
    for a 10_000-file repo before anything was discarded — which is the cost the
    cap exists to avoid.
    """
    seen: list[tuple[str, tuple[object, ...]]] = []
    original = conn.fetch

    async def recording(sql: str, *args: object) -> list[dict[str, object]]:
        seen.append((sql, args))
        return await original(sql, *args)

    conn.fetch = recording  # type: ignore[method-assign]
    await client.get(f"/repos/{REPO_ID}/architecture")

    limits = [args[-1] for sql, args in seen if "scoped" in sql]
    assert limits == [ARCH_MAX_NODES, ARCH_MAX_EDGES]


async def test_architecture_404s_for_a_repo_the_caller_does_not_own(
    other_client: httpx.AsyncClient,
) -> None:
    """§13.5 again, and it must be 404 — a 403 confirms the id names a real repo."""
    resp = await other_client.get(f"/repos/{REPO_ID}/architecture")
    assert resp.status_code == 404


async def test_architecture_404s_for_an_unknown_repo(
    client: httpx.AsyncClient,
) -> None:
    resp = await client.get(f"/repos/{UNKNOWN_REPO_ID}/architecture")
    assert resp.status_code == 404


async def test_architecture_requires_a_session(anon_client: httpx.AsyncClient) -> None:
    resp = await anon_client.get(f"/repos/{REPO_ID}/architecture")
    assert resp.status_code == 401


# --- §18.3 coverage --------------------------------------------------------


async def test_coverage_groups_every_test_under_the_symbol_it_reaches(
    client: httpx.AsyncClient,
) -> None:
    resp = await client.get(f"/repos/{REPO_ID}/coverage?path={FILE_PATH}")
    assert resp.status_code == 200
    body = resp.json()

    assert body["path"] == FILE_PATH
    by_symbol = {s["qualname"]: s for s in body["covered"]}
    # The flat (symbol, test) join collapses to one entry per symbol carrying
    # both of its tests — the case a naive one-row-per-pair response gets wrong.
    assert len(by_symbol["pkg.auth.verify_token"]["tests"]) == 2
    assert {t["qualname"] for t in by_symbol["pkg.auth.verify_token"]["tests"]} == {
        "tests.test_auth.test_verify_token_ok",
        "tests.test_auth.test_verify_token_expired",
    }
    assert by_symbol["pkg.auth.verify_token"]["kind"] == "function"
    assert by_symbol["pkg.auth.Signer"]["start_line"] == 5


async def test_coverage_preserves_source_order(client: httpx.AsyncClient) -> None:
    """Symbols come back in definition order, so the viewer can walk the file."""
    body = (await client.get(f"/repos/{REPO_ID}/coverage?path={FILE_PATH}")).json()
    starts = [s["start_line"] for s in body["covered"]]
    assert starts == sorted(starts)


async def test_coverage_of_an_implementation_file_covers_nothing(
    client: httpx.AsyncClient,
) -> None:
    """`covers` is empty for a non-test file, and that is the true answer.

    An implementation file does not "cover" anything; reporting its outgoing
    call edges here would be a different question wearing this one's name.
    """
    body = (await client.get(f"/repos/{REPO_ID}/coverage?path={FILE_PATH}")).json()
    assert body["covers"] == []


async def test_coverage_of_a_test_file_reports_what_it_reaches(
    client: httpx.AsyncClient,
) -> None:
    body = (
        await client.get(f"/repos/{REPO_ID}/coverage?path={TEST_FILE_PATH}")
    ).json()
    assert [c["qualname"] for c in body["covers"]] == ["pkg.auth.verify_token"]


async def test_coverage_of_an_unknown_path_is_empty_not_404(
    client: httpx.AsyncClient,
) -> None:
    """Deliberate: a 404 here would be an existence oracle for paths.

    "This file is not indexed" and "no test reaches this file" are the same
    answer to the question asked, and separating them tells an unauthorised
    prober which paths exist — the §13.5 reasoning one level down.
    """
    resp = await client.get(f"/repos/{REPO_ID}/coverage?path=does/not/exist.py")
    assert resp.status_code == 200
    assert resp.json() == {
        "path": "does/not/exist.py",
        "covered": [],
        "covers": [],
        "truncated": False,
    }


async def test_coverage_passes_the_spec_cap_to_sql(
    client: httpx.AsyncClient, conn: FakeConn
) -> None:
    seen: list[tuple[str, tuple[object, ...]]] = []
    original = conn.fetch

    async def recording(sql: str, *args: object) -> list[dict[str, object]]:
        seen.append((sql, args))
        return await original(sql, *args)

    conn.fetch = recording  # type: ignore[method-assign]
    await client.get(f"/repos/{REPO_ID}/coverage?path={FILE_PATH}")

    limits = [args[-1] for sql, args in seen if "is_test" in sql]
    assert limits == [COVERAGE_MAX_LINKS, COVERAGE_MAX_LINKS]


async def test_coverage_requires_a_path(client: httpx.AsyncClient) -> None:
    resp = await client.get(f"/repos/{REPO_ID}/coverage")
    assert resp.status_code == 422


async def test_coverage_404s_for_a_repo_the_caller_does_not_own(
    other_client: httpx.AsyncClient,
) -> None:
    resp = await other_client.get(f"/repos/{REPO_ID}/coverage?path={FILE_PATH}")
    assert resp.status_code == 404


async def test_coverage_requires_a_session(anon_client: httpx.AsyncClient) -> None:
    resp = await anon_client.get(f"/repos/{REPO_ID}/coverage?path={FILE_PATH}")
    assert resp.status_code == 401


async def test_neither_view_returns_a_code_body(client: httpx.AsyncClient) -> None:
    """Both views are pointers, like §9 tool_results — never file contents.

    The one endpoint that serves code is `/files`, which is where the caching
    and the line-range cap live.
    """
    arch = (await client.get(f"/repos/{REPO_ID}/architecture")).text
    cov = (await client.get(f"/repos/{REPO_ID}/coverage?path={FILE_PATH}")).text
    assert "SECRET_SENTINEL_VALUE" not in arch
    assert "SECRET_SENTINEL_VALUE" not in cov
