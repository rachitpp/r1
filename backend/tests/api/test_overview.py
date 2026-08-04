"""The §19 overview: lazy generation, generate-once, and the prompt's shape.

The expensive thing here is a model call on a 20-per-day budget, so the tests
that matter most are the ones about *not* making it twice.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.agent.prompts import overview_brief
from app.exceptions import AgentError
from tests.api.fakes import (
    REPO_ID,
    UNKNOWN_REPO_ID,
    FakeArq,
    FakeConn,
)

# --- §19.4 lifecycle -------------------------------------------------------


async def test_first_view_claims_the_row_and_enqueues_one_job(
    client: httpx.AsyncClient, conn: FakeConn, arq: FakeArq
) -> None:
    resp = await client.get(f"/repos/{REPO_ID}/overview")

    assert resp.status_code == 202
    assert resp.json()["status"] == "generating"
    assert resp.json()["body"] is None
    assert [j for j in arq.jobs if j[0] == "generate_overview"] == [
        ("generate_overview", (str(REPO_ID),))
    ]


async def test_polling_while_generating_does_not_enqueue_again(
    client: httpx.AsyncClient, arq: FakeArq
) -> None:
    """The whole point. A page that polls every 2s must not spend 30 requests.

    The claim is a primary-key insert, so the second caller loses the conflict
    and never reaches `enqueue_job` — the database arbitrates, not a flag
    somebody has to remember to check.
    """
    for _ in range(5):
        assert (await client.get(f"/repos/{REPO_ID}/overview")).status_code == 202

    assert len([j for j in arq.jobs if j[0] == "generate_overview"]) == 1


async def test_concurrent_first_views_still_enqueue_once(
    client: httpx.AsyncClient, arq: FakeArq
) -> None:
    """Two browsers opening the same repo at the same instant."""
    await asyncio.gather(
        *(client.get(f"/repos/{REPO_ID}/overview") for _ in range(4))
    )
    assert len([j for j in arq.jobs if j[0] == "generate_overview"]) == 1


async def test_ready_overview_is_returned_with_200(
    client: httpx.AsyncClient, conn: FakeConn, arq: FakeArq
) -> None:
    await client.get(f"/repos/{REPO_ID}/overview")  # claim
    conn.overviews[REPO_ID].update(
        status="ready",
        body="## What this is\nA client [pkg/auth.py:1-2].",
        citations='[{"file_path": "pkg/auth.py", "start_line": 1, "end_line": 2}]',
        model="mistral-medium-latest",
    )

    resp = await client.get(f"/repos/{REPO_ID}/overview")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["citations"] == [
        {"file_path": "pkg/auth.py", "start_line": 1, "end_line": 2}
    ]
    assert body["model"] == "mistral-medium-latest"
    # Still exactly one job: a ready row is never regenerated, because the
    # snapshot it describes cannot change (§14.3).
    assert len([j for j in arq.jobs if j[0] == "generate_overview"]) == 1


async def test_a_failed_overview_surfaces_its_error_and_does_not_retry_itself(
    client: httpx.AsyncClient, conn: FakeConn, arq: FakeArq
) -> None:
    await client.get(f"/repos/{REPO_ID}/overview")
    conn.overviews[REPO_ID].update(status="failed", error="AgentError: no API key")

    resp = await client.get(f"/repos/{REPO_ID}/overview")

    assert resp.status_code == 200
    assert resp.json()["status"] == "failed"
    assert resp.json()["error"] == "AgentError: no API key"
    # A failure must not become a retry loop that drains the daily budget.
    assert len([j for j in arq.jobs if j[0] == "generate_overview"]) == 1


async def test_retry_clears_the_failed_row_and_generates_again(
    client: httpx.AsyncClient, conn: FakeConn, arq: FakeArq
) -> None:
    """`?retry=true` is the only way past a failure — explicit, never automatic.

    A `failed` row that could not be cleared would block this snapshot's
    overview forever, which is precisely the bug `010` had to fix for
    snapshots: an unconditional constraint turning one failure into a permanent
    one.
    """
    await client.get(f"/repos/{REPO_ID}/overview")
    conn.overviews[REPO_ID].update(status="failed", error="boom")

    resp = await client.get(f"/repos/{REPO_ID}/overview?retry=true")

    assert resp.status_code == 202
    assert resp.json()["status"] == "generating"
    assert len([j for j in arq.jobs if j[0] == "generate_overview"]) == 2


async def test_retry_does_not_discard_a_ready_overview(
    client: httpx.AsyncClient, conn: FakeConn, arq: FakeArq
) -> None:
    """Only `failed` rows are clearable — a stray `?retry=true` is harmless."""
    await client.get(f"/repos/{REPO_ID}/overview")
    conn.overviews[REPO_ID].update(status="ready", body="## What this is\nkept")

    resp = await client.get(f"/repos/{REPO_ID}/overview?retry=true")

    assert resp.status_code == 200
    assert resp.json()["body"] == "## What this is\nkept"
    assert len([j for j in arq.jobs if j[0] == "generate_overview"]) == 1


# --- tenancy ---------------------------------------------------------------


async def test_overview_404s_for_a_repo_the_caller_does_not_own(
    other_client: httpx.AsyncClient, arq: FakeArq
) -> None:
    resp = await other_client.get(f"/repos/{REPO_ID}/overview")
    assert resp.status_code == 404
    # And crucially spends nothing: ownership is checked before the claim.
    assert [j for j in arq.jobs if j[0] == "generate_overview"] == []


async def test_overview_404s_for_an_unknown_repo(client: httpx.AsyncClient) -> None:
    assert (await client.get(f"/repos/{UNKNOWN_REPO_ID}/overview")).status_code == 404


async def test_overview_requires_a_session(anon_client: httpx.AsyncClient) -> None:
    assert (await anon_client.get(f"/repos/{REPO_ID}/overview")).status_code == 401


# --- §19.3 the prompt ------------------------------------------------------


FACTS = {
    "name": "encode/httpx",
    "url": "https://github.com/encode/httpx",
    "commit": "b5addb64",
    "n_files": 60,
    "top_dirs": ["httpx"],
    "modules": [
        {
            "path": "httpx/_exceptions.py",
            "n_symbols": 42,
            "fan_in": 80,
            "fan_out": 2,
            "start_line": 1,
            "end_line": 380,
        }
    ],
    "n_modules_total": 23,
    "entry_points": [
        {
            "path": "httpx/__main__.py",
            "fan_in": 0,
            "fan_out": 4,
            "named": True,
            "start_line": 1,
            "end_line": 40,
        }
    ],
    "public_api": [
        {
            "qualname": "httpx.Client",
            "kind": "class",
            "file_path": "httpx/__init__.py",
            "start_line": 10,
            "end_line": 12,
        }
    ],
    "key_symbols": [
        {
            "qualname": "httpx._models.Response",
            "kind": "class",
            "file_path": "httpx/_models.py",
            "start_line": 32,
            "end_line": 40,
            "refs": 57,
        }
    ],
}


def test_the_brief_writes_citations_in_the_exact_answer_format() -> None:
    """Ranges are handed over pre-formatted, not as three fields to assemble.

    The model has to reproduce `[path:start-end]` verbatim (§7.5), and it does
    that far more reliably when the string it must copy is already in front of
    it than when it has to build one from a path, a start and an end.
    """
    brief = overview_brief(FACTS)
    assert "[httpx/_models.py:32-40]" in brief
    assert "[httpx/__init__.py:10-12]" in brief


def test_every_entry_point_is_offered_a_citable_range() -> None:
    """Regression, from the first live run against httpx.

    Entry points were the one fact group with no line range, and the model did
    not leave them uncited — it invented `[httpx/_transports/asgi.py:1-1]` for
    each. Nothing was fabricated *into* the answer (the range failed validation
    and was dropped) but the claim lost its citation, which is the same loss.
    A fact you want cited has to arrive with something to cite.

    The same defect appeared once more, a fact group over: with no range on the
    modules, the second run wrote the literal `[httpx/_models.py:1-?]`. So this
    covers both — every group the prompt invites a citation from ships one.
    """
    brief = overview_brief(FACTS)
    assert "[httpx/__main__.py:1-40]" in brief
    assert "[httpx/_exceptions.py:1-380]" in brief


def test_the_overview_prompt_shows_the_citation_format_it_demands() -> None:
    """The worked contrast is what makes the rule stick, not the rule.

    The first version stated "never a comma-separated list" in its own prose and
    the model produced `[a.py:1-2,3-4,5-6]` for nearly every claim — 2 of ~15
    citations survived. The chat prompt's CITATIONS block carries a CORRECT /
    INCORRECT pair; sharing it is both the fix and one contract in one place.
    """
    from app.agent.prompts import CITATIONS, OVERVIEW_SYSTEM

    assert CITATIONS in OVERVIEW_SYSTEM
    assert "INCORRECT" in OVERVIEW_SYSTEM


def test_the_brief_carries_every_signal_the_prompt_asks_about() -> None:
    brief = overview_brief(FACTS)
    for expected in (
        "encode/httpx",
        "b5addb64",
        "httpx/_exceptions.py",
        "depended on by 80",
        "top 1 of 23",  # honest about being a head, not the whole ranking
        "httpx/__main__.py",
        "conventional name",
        "referenced 57x",
    ):
        assert expected in brief, expected


def test_the_brief_survives_a_repo_with_no_graph_at_all() -> None:
    """A tiny repo resolves no edges and defines no package API.

    Real case — several snapshots in the live database look like this — and the
    prompt must still be well-formed rather than carrying empty headings the
    model then invents content for.
    """
    empty = {
        **FACTS,
        "modules": [],
        "entry_points": [],
        "public_api": [],
        "key_symbols": [],
    }
    brief = overview_brief(empty)
    assert "(none identified)" in brief
    assert brief.count("(none)") == 2


async def test_gather_facts_rejects_an_unknown_snapshot(conn: FakeConn) -> None:
    from app.agent.overview import gather_facts

    with pytest.raises(AgentError):
        await gather_facts(conn, UNKNOWN_REPO_ID)  # type: ignore[arg-type]
