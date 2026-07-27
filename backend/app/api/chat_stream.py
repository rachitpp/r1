"""Agent run -> SPEC §9 SSE events.

Transport only. The graph, prompts, and tools are Phase 3's and are used exactly
as they are: this module subscribes to LangGraph's ``astream_events`` and
translates what comes out into the §9 schema, in §9's order::

    status(thinking) -> [tool_call | tool_result | text]* -> citations -> done
                                                          -> error (on failure)

Three rules from §9 are enforced here:

* ``tool_result`` payloads carry summaries and locations only — never code
  bodies (see :mod:`app.api.tool_events`).
* Citations come from Phase 3's parser (§7.5) run over the final answer and
  validated against the ``files`` table, so a fabricated path never ships.
* The 8-call cap is the graph's (§7.2). This layer counts tool calls to report
  ``tool_calls_used``; it does not enforce anything, because two enforcers
  disagreeing is worse than one.

**Text granularity.** ``model_node`` calls ``ainvoke``, not ``astream``, so the
provider hands back a whole message and there are no token deltas to forward:
each ``text`` event is one complete assistant message. That is a property of the
Phase 3 graph, which Phase 4 is explicitly not allowed to restructure — the fix
is a one-line change in ``app/agent/graph.py`` (``ainvoke`` -> accumulate
``astream``) whenever token-level typing is wanted in the UI. Tool events, which
are the ones that make the agent's work visible, stream live either way.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import asyncpg
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from app.agent.citations import parse_citations, validate_citations
from app.agent.graph import AgentState, build_graph, repo_facts
from app.agent.prompts import system_prompt
from app.api.tool_events import summarize_tool_result
from app.config import AGENT_TOOL_CAP

logger = logging.getLogger(__name__)

SSEEvent = dict[str, str]


def _event(name: str, payload: dict[str, Any]) -> SSEEvent:
    """One §9 event, ready for ``EventSourceResponse``."""
    return {"event": name, "data": json.dumps(payload, default=str)}


def _text_of(message: Any) -> str:
    """Best-effort text of a LangChain message across content shapes.

    ``message.text`` is a str-subclass accessor in langchain-core 1.x that is
    still callable for backwards compatibility — calling it is deprecated, so the
    ``isinstance`` check has to come first.
    """
    text = getattr(message, "text", None)
    if isinstance(text, str):
        return str(text)
    if callable(text):
        return str(text())
    content = getattr(message, "content", "")
    return content if isinstance(content, str) else str(content)


def _tool_output_text(output: Any) -> str:
    """Tool return value as its JSON string, whatever wrapper it arrives in."""
    if isinstance(output, str):
        return output
    content = getattr(output, "content", None)
    if isinstance(content, str):
        return content
    return json.dumps(output, default=str)


async def chat_event_stream(
    model: BaseChatModel,
    conn: asyncpg.Connection,
    repo_id: UUID,
    question: str,
    *,
    tool_cap: int = AGENT_TOOL_CAP,
) -> AsyncIterator[SSEEvent]:
    """Run the agent for ``question`` and yield §9 events as they happen."""
    yield _event("status", {"state": "thinking"})

    n_calls = 0
    answer = ""
    try:
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

        # Tool calls are matched to their results by run_id: parallel calls in
        # one assistant turn interleave, so positional pairing would mislabel
        # them (SPEC §7.2 allows several calls per turn).
        call_n: dict[str, int] = {}
        call_tool: dict[str, str] = {}
        # Model runs that already emitted token deltas. Today none do (see the
        # module docstring), but if the graph ever streams, the end-of-message
        # event must not re-send text the client already has.
        streamed_runs: set[str] = set()

        async for ev in app.astream_events(initial, version="v2"):
            kind = ev["event"]
            if kind == "on_tool_start":
                n_calls += 1
                run_id = str(ev.get("run_id", ""))
                tool_name = str(ev.get("name", "tool"))
                call_n[run_id] = n_calls
                call_tool[run_id] = tool_name
                yield _event(
                    "tool_call",
                    {
                        "n": n_calls,
                        "tool": tool_name,
                        "args": ev.get("data", {}).get("input", {}),
                    },
                )
            elif kind == "on_tool_end":
                run_id = str(ev.get("run_id", ""))
                tool_name = call_tool.get(run_id, str(ev.get("name", "tool")))
                summary, locations = summarize_tool_result(
                    _tool_output_text(ev.get("data", {}).get("output"))
                )
                yield _event(
                    "tool_result",
                    {
                        "n": call_n.get(run_id, n_calls),
                        "tool": tool_name,
                        "summary": summary,
                        "locations": locations,
                    },
                )
            elif kind == "on_chat_model_stream":
                delta = _text_of(ev.get("data", {}).get("chunk"))
                if delta:
                    streamed_runs.add(str(ev.get("run_id", "")))
                    yield _event("text", {"delta": delta})
            elif kind == "on_chat_model_end":
                message = ev.get("data", {}).get("output")
                text = _text_of(message)
                # A message with tool calls is the model narrating its plan; the
                # answer is the last one that asks for nothing further (§7.5).
                if text and not getattr(message, "tool_calls", None):
                    answer = text
                if text and str(ev.get("run_id", "")) not in streamed_runs:
                    yield _event("text", {"delta": text})

        citations = await validate_citations(conn, repo_id, parse_citations(answer))
        yield _event("citations", {"citations": citations})
        yield _event("done", {"tool_calls_used": n_calls})
    except Exception as exc:  # noqa: BLE001 — the client gets one error event
        logger.exception("chat stream failed for repo %s", repo_id)
        yield _event("error", {"message": f"{type(exc).__name__}: {exc}"})
