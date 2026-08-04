"""§23 conversations — the persistence and scoping half of multi-turn memory.

The context-window shaping is pinned in `tests/agent/test_history_context.py`
against the pure function. What is left here is the part with a boundary: whose
conversation it is, which corpus it belongs to, and that a resumed transcript
needs no model call to render.
"""

from __future__ import annotations

import httpx

from tests.api.fakes import (
    CONVO_ID,
    FILE_PATH,
    INDEXING_REPO_ID,
    REPO_ID,
    UNKNOWN_REPO_ID,
    FakeConn,
)

UNKNOWN_CONVO = "77777777-7777-7777-7777-777777777777"


# --- listing and resuming --------------------------------------------------


async def test_lists_this_user_s_conversations_for_this_repo(
    client: httpx.AsyncClient,
) -> None:
    resp = await client.get(f"/repos/{REPO_ID}/conversations")
    assert resp.status_code == 200
    convos = resp.json()["conversations"]
    assert [c["id"] for c in convos] == [str(CONVO_ID)]
    assert convos[0]["title"] == "how does auth work?"
    assert convos[0]["n_turns"] == 1


async def test_another_tenant_sees_none_of_them(
    other_client: httpx.AsyncClient,
) -> None:
    """§13.5: the list is scoped to the caller, not merely filtered in the UI."""
    resp = await other_client.get(f"/repos/{REPO_ID}/conversations")
    assert resp.status_code == 404


async def test_resuming_returns_the_full_transcript(
    client: httpx.AsyncClient,
) -> None:
    resp = await client.get(f"/repos/{REPO_ID}/conversations/{CONVO_ID}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "how does auth work?"
    assert [t["ordinal"] for t in body["turns"]] == [1]
    assert body["turns"][0]["answer"] == "It verifies a token."


async def test_a_resumed_turn_carries_its_stored_citations(
    client: httpx.AsyncClient,
) -> None:
    """Stored validated, so resuming costs no model call and no re-validation."""
    body = (await client.get(f"/repos/{REPO_ID}/conversations/{CONVO_ID}")).json()
    cites = body["turns"][0]["citations"]
    assert cites == [{"file_path": FILE_PATH, "start_line": 1, "end_line": 2}]


async def test_a_resumed_transcript_has_no_tool_timeline(
    client: httpx.AsyncClient,
) -> None:
    """§23.1 stores conclusions, not the searches that produced them."""
    body = (await client.get(f"/repos/{REPO_ID}/conversations/{CONVO_ID}")).json()
    assert "steps" not in body["turns"][0]
    assert "tool_calls" not in body["turns"][0]


# --- the boundary ----------------------------------------------------------


async def test_an_unknown_conversation_is_404(client: httpx.AsyncClient) -> None:
    resp = await client.get(f"/repos/{REPO_ID}/conversations/{UNKNOWN_CONVO}")
    assert resp.status_code == 404


async def test_someone_else_s_conversation_is_404(
    other_client: httpx.AsyncClient,
) -> None:
    """Same status as "does not exist" — the id must not be a probe."""
    resp = await other_client.get(f"/repos/{UNKNOWN_REPO_ID}/conversations/{CONVO_ID}")
    assert resp.status_code == 404


async def test_a_conversation_cannot_be_read_through_another_snapshot(
    client: httpx.AsyncClient,
) -> None:
    """§23.1's third predicate.

    A conversation belongs to a corpus: its stored citations resolve against one
    immutable snapshot, so replaying it against another would cite lines that no
    longer mean the same thing. The snapshot is part of the lookup, not a filter
    applied afterwards.
    """
    resp = await client.get(f"/repos/{INDEXING_REPO_ID}/conversations/{CONVO_ID}")
    assert resp.status_code == 404


async def test_conversations_require_a_session(
    anon_client: httpx.AsyncClient,
) -> None:
    assert (await anon_client.get(f"/repos/{REPO_ID}/conversations")).status_code == 401


async def test_the_owner_can_forget_a_conversation(
    client: httpx.AsyncClient,
) -> None:
    assert (
        await client.delete(f"/repos/{REPO_ID}/conversations/{CONVO_ID}")
    ).status_code == 204
    assert (
        await client.get(f"/repos/{REPO_ID}/conversations/{CONVO_ID}")
    ).status_code == 404


async def test_deleting_someone_else_s_conversation_is_404(
    other_client: httpx.AsyncClient,
) -> None:
    resp = await other_client.delete(f"/repos/{UNKNOWN_REPO_ID}/conversations/{CONVO_ID}")
    assert resp.status_code == 404


# --- what chat does with it ------------------------------------------------


async def test_chat_opens_a_conversation_when_none_is_given(
    client: httpx.AsyncClient, conn: FakeConn
) -> None:
    """The id must exist before the stream starts, so a disconnect is resumable."""
    before = set(conn.conversations)
    async with client.stream(
        "POST", f"/repos/{REPO_ID}/chat", json={"question": "what is auth?"}
    ) as resp:
        assert resp.status_code == 200
        async for _ in resp.aiter_lines():
            pass
    created = set(conn.conversations) - before
    assert len(created) == 1
    assert conn.conversations[created.pop()]["row"]["title"] == "what is auth?"


async def test_chat_stores_the_completed_turn(
    client: httpx.AsyncClient, conn: FakeConn
) -> None:
    async with client.stream(
        "POST",
        f"/repos/{REPO_ID}/chat",
        json={"question": "follow up", "conversation_id": str(CONVO_ID)},
    ) as resp:
        assert resp.status_code == 200
        async for _ in resp.aiter_lines():
            pass
    turns = conn.conversations[CONVO_ID]["turns"]
    assert [t["question"] for t in turns] == ["how does auth work?", "follow up"]
    assert turns[-1]["ordinal"] == 2


async def test_chat_against_an_unknown_conversation_is_404(
    client: httpx.AsyncClient,
) -> None:
    resp = await client.post(
        f"/repos/{REPO_ID}/chat",
        json={"question": "q", "conversation_id": UNKNOWN_CONVO},
    )
    assert resp.status_code == 404


async def test_chat_rejects_a_conversation_from_another_snapshot(
    client: httpx.AsyncClient,
) -> None:
    """Decided before a slot is taken or a token is spent."""
    resp = await client.post(
        f"/repos/{INDEXING_REPO_ID}/chat",
        json={"question": "q", "conversation_id": str(CONVO_ID)},
    )
    assert resp.status_code in (404, 409)
