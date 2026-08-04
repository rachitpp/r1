"""§9 SSE stream: the event sequence, and the no-code-bodies rule.

Networkless but end-to-end through the real machinery: the graph dispatches a
real tool against the fake connection, the real §7.5 parser extracts citations,
and the §9 adapter shapes every event. Only the provider call is scripted.
"""

from __future__ import annotations

import json
import uuid

import httpx

from tests.api.fakes import FILE_CONTENT, FILE_PATH, REPO_ID, FakeConn


async def collect_events(
    client: httpx.AsyncClient, question: str = "how are tokens verified?"
) -> list[tuple[str, dict]]:
    """Stream a chat and parse the wire format into ``(event, data)`` pairs."""
    events: list[tuple[str, dict]] = []
    async with client.stream(
        "POST", f"/repos/{REPO_ID}/chat", json={"question": question}
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        name: str | None = None
        async for line in resp.aiter_lines():
            if line.startswith("event:"):
                name = line.split(":", 1)[1].strip()
            elif line.startswith("data:") and name is not None:
                events.append((name, json.loads(line.split(":", 1)[1].strip())))
                name = None
    return events


async def test_event_sequence_matches_spec_order(client: httpx.AsyncClient) -> None:
    events = await collect_events(client)
    names = [name for name, _ in events]

    assert names[0] == "status"
    assert events[0][1] == {"state": "thinking"}
    assert names[-2:] == ["citations", "done"]
    # tool_call precedes its result, and text arrives after the tool round-trip.
    assert names.index("tool_call") < names.index("tool_result") < names.index("text")
    assert "error" not in names


async def test_tool_events_carry_the_call_number_and_tool_name(
    client: httpx.AsyncClient,
) -> None:
    events = dict(await collect_events(client))
    assert events["tool_call"]["n"] == 1
    assert events["tool_call"]["tool"] == "read_file"
    assert events["tool_call"]["args"]["path"] == FILE_PATH
    assert events["tool_result"]["n"] == 1
    assert events["tool_result"]["tool"] == "read_file"


async def test_tool_result_carries_locations_not_code(
    client: httpx.AsyncClient,
) -> None:
    """§9: summaries and locations only — never a code body over the wire."""
    events = await collect_events(client)
    payloads = [data for name, data in events if name == "tool_result"]
    assert payloads
    for data in payloads:
        assert set(data) == {"n", "tool", "summary", "locations"}
        blob = json.dumps(data)
        assert "SECRET_SENTINEL_VALUE" not in blob
        assert FILE_CONTENT.strip() not in blob
    assert payloads[0]["locations"] == [
        {"file_path": FILE_PATH, "start_line": 1, "end_line": 2}
    ]
    assert payloads[0]["summary"] == f"{FILE_PATH}:1-2"


async def test_citations_are_validated_against_the_files_table(
    client: httpx.AsyncClient,
) -> None:
    """The real path survives; the fabricated one is dropped (§7.5)."""
    events = dict(await collect_events(client))
    assert events["citations"]["citations"] == [
        {"file_path": FILE_PATH, "start_line": 1, "end_line": 2}
    ]


async def test_done_reports_tool_calls_used(client: httpx.AsyncClient) -> None:
    """Exact equality on purpose: §9 is a frozen contract, so a field appearing
    here should have to be intended. `conversation_id` was added by §23.3."""
    events = dict(await collect_events(client))
    assert set(events["done"]) == {"tool_calls_used", "conversation_id"}
    assert events["done"]["tool_calls_used"] == 1


async def test_done_carries_the_conversation_it_landed_in(
    client: httpx.AsyncClient, conn: FakeConn
) -> None:
    """§23.3 — the client captures this on turn one and sends it thereafter.

    It names a conversation that really exists: handing back an id for a thread
    with no turns would make the next question append to a phantom.
    """
    events = dict(await collect_events(client))
    convo_id = events["done"]["conversation_id"]
    assert convo_id
    assert uuid.UUID(convo_id) in conn.conversations


async def test_text_events_deliver_the_answer(client: httpx.AsyncClient) -> None:
    events = await collect_events(client)
    text = "".join(data["delta"] for name, data in events if name == "text")
    assert f"[{FILE_PATH}:1-2]" in text
    assert "Tokens are verified" in text


async def test_citations_event_carries_grounding_verdicts(
    client: httpx.AsyncClient,
) -> None:
    """§27 rides along on the existing event rather than adding a tenth one."""
    events = dict(await collect_events(client))
    grounding = events["citations"]["grounding"]
    # One verdict per surviving citation — the fabricated path is dropped before
    # grounding ever sees it.
    assert len(grounding) == len(events["citations"]["citations"])
    assert grounding[0]["file_path"] == FILE_PATH


async def test_a_claim_naming_no_code_is_unchecked_not_unsupported(
    client: httpx.AsyncClient,
) -> None:
    """The scripted claim is prose ("Tokens are verified in"), so there is
    nothing to match. Reporting that as *unsupported* would invent a warning
    out of the method's own blind spot, which is the failure that would make
    the signal worth ignoring."""
    events = dict(await collect_events(client))
    assert events["citations"]["grounding"][0]["verdict"] == "unchecked"
