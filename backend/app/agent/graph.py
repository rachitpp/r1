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

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, TypedDict
from uuid import UUID

import asyncpg
from langchain_core.language_models.chat_models import BaseChatModel
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

logger = logging.getLogger(__name__)


class AgentState(TypedDict):
    """Loop state (SPEC §7.2)."""

    repo_id: str
    question: str
    messages: Annotated[list[AnyMessage], add_messages]
    tool_calls_used: int
    citations: list[Citation]


def build_tools(conn: asyncpg.Connection, repo_id: UUID) -> list[StructuredTool]:
    """Bind ``conn``/``repo_id`` into the six tools (SPEC §7.1).

    The model never sees the connection or the repo id — they are closure
    state, not parameters it could get wrong.
    """

    async def search_code(query: str, k: int = 10) -> str:
        return json.dumps(await t.search_code(conn, repo_id, query, k))

    async def read_file(
        path: str, start_line: int | None = None, end_line: int | None = None
    ) -> str:
        return json.dumps(await t.read_file(conn, repo_id, path, start_line, end_line))

    async def get_definition(symbol: str) -> str:
        return json.dumps(await t.get_definition(conn, repo_id, symbol))

    async def find_references(symbol: str, kind: str | None = None) -> str:
        return json.dumps(await t.find_references(conn, repo_id, symbol, kind))

    async def expand_context(
        symbol: str, depth: int = 1, direction: str = "out"
    ) -> str:
        return json.dumps(
            await t.expand_context(conn, repo_id, symbol, depth, direction)
        )

    async def list_directory(path: str = "") -> str:
        return json.dumps(await t.list_directory(conn, repo_id, path))

    specs: list[tuple[Callable[..., Awaitable[str]], str]] = [
        (search_code, "Search the repository's implementation code semantically. "
                      "Best for 'where does X happen' questions. Returns ranked "
                      "chunks with file paths and line numbers."),
        (read_file, "Read a file's exact contents, line-numbered. Pass start_line "
                    "and end_line for a range; whole-file reads are capped."),
        (get_definition, "Look up where a symbol is defined, by name or dotted "
                         "qualname. A direct index lookup — use this when you "
                         "already know the identifier."),
        (find_references, "Find what calls, imports, or subclasses a symbol. "
                          "Optionally filter by kind: imports|calls|extends."),
        (expand_context, "Traverse the symbol graph from a symbol to the code it "
                         "depends on ('out'), what depends on it ('in'), or both. "
                         "Use this instead of re-searching once you have an entry "
                         "point."),
        (list_directory, "Show the repository tree, two levels deep, with file "
                         "sizes."),
    ]
    return [
        StructuredTool.from_function(coroutine=fn, name=fn.__name__, description=desc)
        for fn, desc in specs
    ]


def _count_tool_calls(message: AnyMessage) -> int:
    return len(getattr(message, "tool_calls", None) or [])


def build_graph(
    model: BaseChatModel,
    conn: asyncpg.Connection,
    repo_id: UUID,
    *,
    tool_cap: int = AGENT_TOOL_CAP,
) -> Any:
    """Compile the agent graph for one repo (SPEC §7.2)."""
    tool_list = build_tools(conn, repo_id)
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
    conn: asyncpg.Connection, repo_id: UUID
) -> tuple[str, int, list[str]]:
    """Name, file count, and top-level dirs for the system prompt (§7.3)."""
    row = await conn.fetchrow("SELECT name FROM repos WHERE id = $1", repo_id)
    name = str(row["name"]) if row else "(unknown)"
    paths = [
        str(r["path"])
        for r in await conn.fetch(
            "SELECT path FROM files WHERE repo_id = $1", repo_id
        )
    ]
    tops = sorted({p.split("/")[0] for p in paths if "/" in p})
    return name, len(paths), tops


async def answer_question(
    model: BaseChatModel,
    conn: asyncpg.Connection,
    repo_id: UUID,
    question: str,
    *,
    tool_cap: int = AGENT_TOOL_CAP,
) -> AgentState:
    """Run the loop end to end and return the final state.

    Citations are parsed from the final answer and validated against the
    ``files`` table, so a fabricated path never reaches the caller (§7.5).
    """
    name, n_files, tops = await repo_facts(conn, repo_id)
    app = build_graph(model, conn, repo_id, tool_cap=tool_cap)
    initial: AgentState = {
        "repo_id": str(repo_id),
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
    final["citations"] = await validate_citations(
        conn, repo_id, parse_citations(answer)
    )
    return final
