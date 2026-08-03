"""Loop mechanics with a scripted fake model (SPEC §7.2) — no network.

The model is the only nondeterministic part of the loop, so it is the part we
replace: a `FakeChatModel` returns a pre-scripted sequence of responses. That
makes tool dispatch, cap enforcement, the forced final answer, and state
accumulation all deterministically testable without an API key or a quota.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.agent.graph import build_graph
from app.agent.prompts import FORCED_ANSWER

REPO_ID = uuid.uuid4()


class FakeChatModel(BaseChatModel):
    """Replays a scripted list of AIMessages; records what it was asked.

    ``bind_tools`` returns ``self`` so the graph's tool-bound and unbound
    handles are the same object — that is what lets a test assert the forced
    final call happened on the *unbound* model.
    """

    responses: list[AIMessage] = []
    calls: list[list[BaseMessage]] = []
    bound_tool_names: list[str] = []

    @property
    def _llm_type(self) -> str:
        return "fake"

    def bind_tools(self, tools: Any, **kwargs: Any) -> BaseChatModel:
        self.bound_tool_names = [t.name for t in tools]
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.calls.append(list(messages))
        idx = min(len(self.calls) - 1, len(self.responses) - 1)
        template = self.responses[idx]
        # A *fresh* message each call. `add_messages` keys on message id, so
        # replaying one instance makes the reducer overwrite instead of append
        # — the loop then sees a stale last message and exits early.
        turn = len(self.calls)
        fresh = AIMessage(
            content=template.content,
            # Unique call ids per turn, as real providers issue them — reusing
            # one id makes request/response pairing ambiguous to assert on.
            tool_calls=[
                {**dict(tc), "id": f"{tc['id']}-{turn}"}
                for tc in (template.tool_calls or [])
            ],
        )
        return ChatResult(generations=[ChatGeneration(message=fresh)])

    async def _agenerate(
        self, messages, stop=None, run_manager=None, **kwargs
    ) -> ChatResult:
        return self._generate(messages, stop, run_manager, **kwargs)


class FakeConn:
    """Enough of asyncpg for tools that only read; unused by these tests."""

    def is_closed(self) -> bool:
        """Real connections have this, and `pool.acquire` probes with it (§DB
        pool liveness). A double standing in for a connection has to implement
        enough of the interface to be one."""
        return False

    async def fetch(self, *_a: object, **_k: object) -> list[dict]:
        return []

    async def fetchrow(self, *_a: object, **_k: object) -> dict | None:
        return None

    async def fetchval(self, *_a: object, **_k: object) -> int:
        return 0


def _tool_call(name: str, args: dict, call_id: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}],
    )


def _state(messages: list[BaseMessage]) -> dict[str, Any]:
    return {
        "repo_id": str(REPO_ID),
        "question": "q",
        "messages": messages,
        "tool_calls_used": 0,
        "citations": [],
    }


@pytest.mark.asyncio
async def test_answers_without_tools_when_model_does_not_call_any() -> None:
    model = FakeChatModel(responses=[AIMessage(content="Direct answer.")], calls=[])
    app = build_graph(model, FakeConn(), REPO_ID)  # type: ignore[arg-type]
    final = await app.ainvoke(_state([]))
    assert final["tool_calls_used"] == 0
    assert final["messages"][-1].content == "Direct answer."


@pytest.mark.asyncio
async def test_dispatches_tool_then_answers() -> None:
    model = FakeChatModel(
        responses=[
            _tool_call("list_directory", {"path": ""}, "c1"),
            AIMessage(content="Answer after one tool."),
        ],
        calls=[],
    )
    app = build_graph(model, FakeConn(), REPO_ID)  # type: ignore[arg-type]
    final = await app.ainvoke(_state([]))
    assert final["tool_calls_used"] == 1
    tool_msgs = [m for m in final["messages"] if m.type == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0].name == "list_directory"
    assert final["messages"][-1].content == "Answer after one tool."


@pytest.mark.asyncio
async def test_all_six_tools_are_bound() -> None:
    model = FakeChatModel(responses=[AIMessage(content="x")], calls=[])
    build_graph(model, FakeConn(), REPO_ID)  # type: ignore[arg-type]
    assert set(model.bound_tool_names) == {
        "search_code",
        "read_file",
        "get_definition",
        "find_references",
        "expand_context",
        "list_directory",
    }


@pytest.mark.asyncio
async def test_cap_is_enforced_and_forces_a_final_answer() -> None:
    """A model that only ever calls tools must still terminate with an answer."""
    model = FakeChatModel(
        # Always asks for another tool — only the cap can stop this.
        responses=[_tool_call("list_directory", {"path": ""}, "loop")],
        calls=[],
    )
    app = build_graph(model, FakeConn(), REPO_ID, tool_cap=3)  # type: ignore[arg-type]
    final = await app.ainvoke(_state([]))
    assert final["tool_calls_used"] == 3, "cap must stop dispatch at exactly the limit"
    assert any(
        m.type == "human" and m.content == FORCED_ANSWER for m in final["messages"]
    ), "forced-answer message must be injected"


@pytest.mark.asyncio
async def test_forced_answer_call_has_no_tools_bound() -> None:
    """The final call goes to the unbound model, so it cannot request more."""
    model = FakeChatModel(
        responses=[_tool_call("list_directory", {"path": ""}, "loop")], calls=[]
    )
    app = build_graph(model, FakeConn(), REPO_ID, tool_cap=1)  # type: ignore[arg-type]
    await app.ainvoke(_state([]))
    last_prompt = model.calls[-1]
    assert last_prompt[-1].content == FORCED_ANSWER


@pytest.mark.asyncio
async def test_unknown_tool_returns_error_not_exception() -> None:
    model = FakeChatModel(
        responses=[
            _tool_call("no_such_tool", {}, "c1"),
            AIMessage(content="recovered"),
        ],
        calls=[],
    )
    app = build_graph(model, FakeConn(), REPO_ID)  # type: ignore[arg-type]
    final = await app.ainvoke(_state([]))
    tool_msg = next(m for m in final["messages"] if m.type == "tool")
    assert "error" in json.loads(tool_msg.content)
    assert final["messages"][-1].content == "recovered"


@pytest.mark.asyncio
async def test_parallel_tool_calls_all_dispatch_and_count() -> None:
    multi = AIMessage(
        content="",
        tool_calls=[
            {"name": "list_directory", "args": {"path": ""}, "id": "a", "type": "tool_call"},
            {"name": "list_directory", "args": {"path": "x"}, "id": "b", "type": "tool_call"},
        ],
    )
    model = FakeChatModel(responses=[multi, AIMessage(content="done")], calls=[])
    app = build_graph(model, FakeConn(), REPO_ID)  # type: ignore[arg-type]
    final = await app.ainvoke(_state([]))
    assert final["tool_calls_used"] == 2
    assert len([m for m in final["messages"] if m.type == "tool"]) == 2


@pytest.mark.asyncio
async def test_forced_answer_closes_unanswered_tool_calls() -> None:
    """The cap trips after the model has already requested tools.

    Leaving those calls unanswered makes the history malformed — function
    calls with no responses. Gemini tolerated it; Mistral returns
    `400 invalid_request_message_order`. Each refused call must be closed with
    an explicit "not executed" result before the final call.
    """
    model = FakeChatModel(
        responses=[_tool_call("list_directory", {"path": ""}, "loop")], calls=[]
    )
    app = build_graph(model, FakeConn(), REPO_ID, tool_cap=1)  # type: ignore[arg-type]
    await app.ainvoke(_state([]))

    # Assert on the payload actually sent to the final call — that is the
    # message list the provider validates, and the one Mistral rejected.
    sent = model.calls[-1]
    requested = [
        c["id"] for m in sent if isinstance(m, AIMessage) for c in (m.tool_calls or [])
    ]
    answered = [m.tool_call_id for m in sent if m.type == "tool"]
    assert sorted(requested) == sorted(answered), (
        "forced-answer payload has function calls with no responses"
    )

    # The closer explains itself rather than faking a result.
    closers = [
        json.loads(m.content)
        for m in sent
        if m.type == "tool" and "not executed" in str(m.content)
    ]
    assert closers and "limit" in closers[0]["error"]


@pytest.mark.asyncio
async def test_refused_calls_do_not_count_as_used() -> None:
    """A call we declined to run is not a call we spent."""
    model = FakeChatModel(
        responses=[_tool_call("list_directory", {"path": ""}, "loop")], calls=[]
    )
    app = build_graph(model, FakeConn(), REPO_ID, tool_cap=2)  # type: ignore[arg-type]
    final = await app.ainvoke(_state([]))
    assert final["tool_calls_used"] == 2
