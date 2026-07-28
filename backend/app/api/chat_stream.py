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

**Text granularity is the provider's, not ours.** ``model_node`` calls
``ainvoke``, but ``astream_events`` still reports token-level
``on_chat_model_stream`` chunks for providers whose client streams internally —
Mistral does, and a live run of this endpoint emits ~70 ``text`` deltas for one
answer. A provider that does not stream produces no chunk events at all, and
then the whole message arrives as a single ``text`` delta at
``on_chat_model_end``. Both are handled, and the message-end fallback is
suppressed for any run that already streamed, so no client ever sees the answer
twice.

**Three ways a run can end besides finishing.** The §7.2 cap bounds tool calls,
not time, so a wall clock bounds the rest (``CHAT_TIMEOUT_S``); a client that
closes the tab cancels the run rather than paying for an answer nobody will
read; and anything else becomes one redacted ``error`` event carrying the
request id. All four outcomes release the concurrency slot and land in
``chat_streams_total``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Callable
from typing import Any
from uuid import UUID

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from app import metrics
from app.agent.citations import parse_citations, validate_citations
from app.agent.graph import AgentState, build_graph, repo_facts
from app.agent.prompts import system_prompt
from app.api.tool_events import summarize_tool_result
from app.config import AGENT_TOOL_CAP, get_settings
from app.db.pool import ConnSource, acquire
from app.exceptions import AgentTimeoutError
from app.logging_setup import get_request_id
from app.redact import safe_error_text

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


def _error_payload(exc: BaseException) -> dict[str, str]:
    """The §9 ``error`` payload.

    ``message`` keeps its §9 meaning and is what the frontend renders, but it is
    now redacted (:mod:`app.redact`) rather than a raw ``str(exc)`` — an asyncpg
    failure carries the DSN, and a provider client can echo the credentials it
    just sent. ``request_id`` is added so a user can quote one string that finds
    the full, unredacted server-side log line for their exact run.
    """
    return {"message": safe_error_text(exc), "request_id": get_request_id()}


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
    source: ConnSource,
    repo_id: UUID,
    question: str,
    *,
    tool_cap: int = AGENT_TOOL_CAP,
    timeout_s: float | None = None,
    on_finish: Callable[[], None] | None = None,
) -> AsyncIterator[SSEEvent]:
    """Run the agent for ``question`` and yield §9 events as they happen.

    ``source`` is a pool (the API) or a connection (tests, CLIs); the graph
    borrows from it per tool call rather than holding one for the whole run.

    ``on_finish`` runs exactly once, however the stream ends — normally, on a
    timeout, on a client disconnect, or on an exception. It is how the route's
    concurrency slot gets given back, and a slot that leaks on the unhappy path
    is a server that refuses every request an hour after the first failure.
    """
    started = time.perf_counter()
    outcome = "error"
    n_calls = 0
    answer = ""
    try:
        yield _event("status", {"state": "thinking"})

        timeout = get_settings().CHAT_TIMEOUT_S if timeout_s is None else timeout_s

        # The wall clock the §7.2 tool cap does not provide, over *everything* a
        # run does — the setup queries as much as the model calls, since a
        # database that has stopped answering wedges a stream just as well as a
        # provider that has. `asyncio.timeout` cancels this task at whatever it
        # is awaiting and re-raises as TimeoutError on the way out of the block,
        # so the handler below still gets to send a clean `error` event instead
        # of the client watching a stream that simply stops.
        async with asyncio.timeout(timeout):
            name, n_files, tops = await repo_facts(source, repo_id)
            app = build_graph(model, source, repo_id, tool_cap=tool_cap)
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

            # Tool calls are matched to their results by run_id: parallel calls
            # in one assistant turn interleave, so positional pairing would
            # mislabel them (SPEC §7.2 allows several calls per turn).
            call_n: dict[str, int] = {}
            call_tool: dict[str, str] = {}
            # Model runs that already emitted token deltas. The end-of-message
            # event must not re-send text the client already has, and whether a
            # run streams depends on the provider (see the module docstring).
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
                    # A message with tool calls is the model narrating its plan;
                    # the answer is the last one asking for nothing further (§7.5).
                    if text and not getattr(message, "tool_calls", None):
                        answer = text
                    if text and str(ev.get("run_id", "")) not in streamed_runs:
                        yield _event("text", {"delta": text})

            async with acquire(source) as conn:
                citations = await validate_citations(
                    conn, repo_id, parse_citations(answer)
                )

        yield _event("citations", {"citations": citations})
        yield _event("done", {"tool_calls_used": n_calls})
        outcome = "done"

    except (asyncio.CancelledError, GeneratorExit):
        # The client hung up, or the server is shutting down. Not an error and
        # not ours to answer — but worth counting, because a rising cancelled
        # rate means people are giving up on how long answers take.
        #
        # Both, because the two arrive by different routes: sse-starlette
        # cancels the task on disconnect (CancelledError, delivered even mid
        # provider call), while a consumer that simply closes the generator
        # gets GeneratorExit. Catching only the first would file half of the
        # hang-ups under "error".
        outcome = "cancelled"
        logger.info(
            "chat stream cancelled for repo %s after %d tool call(s)",
            repo_id,
            n_calls,
        )
        raise

    except TimeoutError:
        outcome = "timeout"
        logger.warning(
            "chat stream timed out for repo %s after %.0fs", repo_id, timeout
        )
        timed_out = AgentTimeoutError(f"answer timed out after {timeout:.0f}s")
        yield _event("error", _error_payload(timed_out))

    except Exception as exc:  # noqa: BLE001 — the client gets one error event
        outcome = "error"
        logger.exception("chat stream failed for repo %s", repo_id)
        yield _event("error", _error_payload(exc))

    finally:
        metrics.chat_streams.inc(outcome=outcome)
        metrics.chat_duration.observe(time.perf_counter() - started)
        metrics.chat_tool_calls.observe(n_calls)
        if on_finish is not None:
            on_finish()
