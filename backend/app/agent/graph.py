"""The LangGraph agent loop (SPEC §7.2).

    model -> (tool calls AND used < AGENT_TOOL_CAP) -> tools -> model
          -> otherwise END

On hitting the cap the loop injects a forced-answer message and makes one
final model call **with tools unbound**, so the model cannot request another
call it will not get. That is the difference between a hard cap and a
suggestion.

The graph owns no provider knowledge: it is handed a chat model (from
:mod:`app.agent.model`) and binds tools to it. Anything satisfying
LangChain's `BaseChatModel` works, which is also what makes the scripted
fake-model tests possible without a network.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Imported for typing only: this module pulls in `transformers` and
    # therefore torch — 185 MB measured, more than the embedding model it
    # would sit beside. Deferring it is what keeps an API replica off torch
    # entirely (SPEC §16.1).
    from langchain_core.language_models.chat_models import BaseChatModel

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, TypedDict
from uuid import UUID

from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import StructuredTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from app.agent import tools as t
from app.agent.citations import Citation, parse_citations, validate_citations
from app.agent.prompts import FORCED_ANSWER, system_prompt
from app.config import AGENT_TOOL_CAP
from app.db.pool import ConnSource, acquire

logger = logging.getLogger(__name__)


class AgentState(TypedDict):
    """Loop state (SPEC §7.2)."""

    snapshot_id: str
    question: str
    messages: Annotated[list[AnyMessage], add_messages]
    tool_calls_used: int
    citations: list[Citation]


def build_tools(source: ConnSource, snapshot_id: UUID) -> list[StructuredTool]:
    """Bind ``source``/``snapshot_id`` into the six tools (SPEC §7.1).

    The model never sees the connection or the repo id — they are closure
    state, not parameters it could get wrong.

    ``source`` is a pool or a single connection, and each tool borrows one for
    the length of its own call. That distinction is the difference between an
    answer holding a database connection for its entire two-minute run and
    holding one for the few milliseconds each query actually takes: the API
    passes the pool, and the CLIs pass the connection they already own.
    """

    async def search_code(query: str, k: int = 10) -> str:
        async with acquire(source) as conn:
            return json.dumps(await t.search_code(conn, snapshot_id, query, k))

    async def read_file(
        path: str, start_line: int | None = None, end_line: int | None = None
    ) -> str:
        async with acquire(source) as conn:
            return json.dumps(
                await t.read_file(conn, snapshot_id, path, start_line, end_line)
            )

    async def get_definition(symbol: str) -> str:
        async with acquire(source) as conn:
            return json.dumps(await t.get_definition(conn, snapshot_id, symbol))

    async def find_references(symbol: str, kind: str | None = None) -> str:
        async with acquire(source) as conn:
            return json.dumps(await t.find_references(conn, snapshot_id, symbol, kind))

    async def expand_context(
        symbol: str, depth: int = 1, direction: str = "out"
    ) -> str:
        async with acquire(source) as conn:
            return json.dumps(
                await t.expand_context(conn, snapshot_id, symbol, depth, direction)
            )

    async def list_directory(path: str = "") -> str:
        async with acquire(source) as conn:
            return json.dumps(await t.list_directory(conn, snapshot_id, path))

    # Descriptions state WHEN to call, not just what the tool does. Trigger
    # conditions in the description measurably raise should-call rate, and the
    # first dev-set run showed expand_context and find_references at 0% usage
    # with purely descriptive wording (review doc, tuning iteration 1).
    specs: list[tuple[Callable[..., Awaitable[str]], str]] = [
        (search_code, "Semantic search over implementation code. CALL THIS FIRST "
                      "when you do not yet know which file or symbol is "
                      "involved. Do NOT call it a second time with reworded "
                      "phrasing — a second search returns the same "
                      "neighbourhood. Once you have any symbol name, switch to "
                      "expand_context or get_definition instead."),
        (read_file, "Read exact line-numbered source. Call this when you need "
                    "the precise lines for a citation, or when a chunk you were "
                    "shown is cut off mid-definition. Pass start_line/end_line; "
                    "whole-file reads are capped."),
        (get_definition, "Direct symbol lookup by name or dotted qualname — an "
                         "index hit, not a ranked search. Call this whenever the "
                         "question names an identifier (a class, method, or "
                         "function), instead of searching for it."),
        (find_references, "Who calls, imports, or subclasses this symbol. Call "
                          "this for 'what uses X', 'where is X invoked from', or "
                          "when you need to know whether changing X matters. "
                          "Optional kind filter: imports|calls|extends."),
        (expand_context, "Traverse the symbol graph and return the CODE of the "
                         "neighbours, not just their names. Call this whenever "
                         "the question is about a flow, a sequence, or 'what "
                         "happens when' — those answers live across several "
                         "functions, and this is the only tool that assembles "
                         "them in one call. direction='out' for what a symbol "
                         "calls, 'in' for what calls it, 'both' to see the whole "
                         "neighbourhood. Prefer this over reading each file in "
                         "turn."),
        (list_directory, "Repository tree, two levels deep, with file sizes. "
                         "Call this only when you are unsure of the layout and "
                         "search has not helped."),
    ]
    return [
        StructuredTool.from_function(coroutine=fn, name=fn.__name__, description=desc)
        for fn, desc in specs
    ]


def _count_tool_calls(message: AnyMessage) -> int:
    return len(getattr(message, "tool_calls", None) or [])


def build_graph(
    model: BaseChatModel,
    source: ConnSource,
    snapshot_id: UUID,
    *,
    tool_cap: int = AGENT_TOOL_CAP,
) -> Any:
    """Compile the agent graph for one repo (SPEC §7.2)."""
    tool_list = build_tools(source, snapshot_id)
    by_name = {tool.name: tool for tool in tool_list}
    model_with_tools = model.bind_tools(tool_list)

    async def model_node(state: AgentState) -> dict[str, Any]:
        response = await model_with_tools.ainvoke(state["messages"])
        return {"messages": [response]}

    async def tool_node(state: AgentState) -> dict[str, Any]:
        last = state["messages"][-1]
        calls = getattr(last, "tool_calls", None) or []
        out: list[AnyMessage] = []
        for call in calls:
            name = call["name"]
            tool = by_name.get(name)
            if tool is None:
                payload = json.dumps({"error": f"unknown tool {name!r}"})
            else:
                try:
                    payload = await tool.ainvoke(call["args"])
                except Exception as exc:  # noqa: BLE001 — tools never kill the loop
                    logger.exception("tool %s raised", name)
                    payload = json.dumps({"error": f"{name} failed: {exc}"})
            out.append(ToolMessage(content=payload, tool_call_id=call["id"], name=name))
        return {"messages": out, "tool_calls_used": state["tool_calls_used"] + len(calls)}

    async def forced_answer_node(state: AgentState) -> dict[str, Any]:
        """One final call with tools unbound — the cap is hard, not advisory.

        The cap trips *after* the model has already requested tool calls, so
        the history ends with an assistant turn whose calls we are refusing to
        run. Left as-is that is malformed: function calls with no responses.
        Some providers tolerate it, Mistral returns
        ``400 invalid_request_message_order`` — and Mistral is right. Close
        each unanswered call with an explicit "not executed" result, so the
        conversation is well-formed *and* the model knows why it has no data
        for them.
        """
        last = state["messages"][-1]
        unanswered = getattr(last, "tool_calls", None) or []
        closers: list[AnyMessage] = [
            ToolMessage(
                content=json.dumps(
                    {"error": "not executed: tool call limit reached"}
                ),
                tool_call_id=call["id"],
                name=call["name"],
            )
            for call in unanswered
        ]
        prompt = HumanMessage(content=FORCED_ANSWER)
        response = await model.ainvoke([*state["messages"], *closers, prompt])
        return {"messages": [*closers, prompt, response]}

    def route(state: AgentState) -> str:
        last = state["messages"][-1]
        if _count_tool_calls(last) == 0:
            return END
        if state["tool_calls_used"] >= tool_cap:
            return "forced_answer"
        return "tools"

    graph = StateGraph(AgentState)
    graph.add_node("model", model_node)
    graph.add_node("tools", tool_node)
    graph.add_node("forced_answer", forced_answer_node)
    graph.add_edge(START, "model")
    graph.add_conditional_edges(
        "model", route, {"tools": "tools", "forced_answer": "forced_answer", END: END}
    )
    graph.add_edge("tools", "model")
    graph.add_edge("forced_answer", END)
    return graph.compile()


async def repo_facts(
    source: ConnSource, snapshot_id: UUID
) -> tuple[str, int, list[str]]:
    """Name, file count, and top-level dirs for the system prompt (§7.3)."""
    async with acquire(source) as conn:
        row = await conn.fetchrow("SELECT name FROM repos WHERE id = $1", snapshot_id)
        name = str(row["name"]) if row else "(unknown)"
        paths = [
            str(r["path"])
            for r in await conn.fetch(
                "SELECT path FROM files WHERE snapshot_id = $1", snapshot_id
            )
        ]
    tops = sorted({p.split("/")[0] for p in paths if "/" in p})
    return name, len(paths), tops


async def answer_question(
    model: BaseChatModel,
    source: ConnSource,
    snapshot_id: UUID,
    question: str,
    *,
    tool_cap: int = AGENT_TOOL_CAP,
) -> AgentState:
    """Run the loop end to end and return the final state.

    Citations are parsed from the final answer and validated against the
    ``files`` table, so a fabricated path never reaches the caller (§7.5).
    """
    name, n_files, tops = await repo_facts(source, snapshot_id)
    app = build_graph(model, source, snapshot_id, tool_cap=tool_cap)
    initial: AgentState = {
        "snapshot_id": str(snapshot_id),
        "question": question,
        "messages": [
            SystemMessage(content=system_prompt(name, n_files, tops)),
            HumanMessage(content=question),
        ],
        "tool_calls_used": 0,
        "citations": [],
    }
    final: AgentState = await app.ainvoke(initial)

    answer = ""
    for m in reversed(final["messages"]):
        if isinstance(m, AIMessage) and not (getattr(m, "tool_calls", None) or []):
            answer = m.text() if callable(getattr(m, "text", None)) else str(m.content)
            break
    async with acquire(source) as conn:
        final["citations"] = await validate_citations(
            conn, snapshot_id, parse_citations(answer)
        )
    return final
