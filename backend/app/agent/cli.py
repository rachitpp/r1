"""Agent CLI (SPEC §7, M2).

    python -m app.agent.cli <repo_url_or_id> "question"  [--json] [--tool-cap N]

Streams tool calls as they happen and prints the final answer with validated
citations. ``--json`` emits one machine-readable object instead, for eval.

**Every run also writes its complete trace to ``var/traces/``** (gitignored).
A model call is a metered resource — on some free tiers, one of twenty per day
— so a trace lost to a terminal pipe costs real quota to recreate. Learned the
hard way in M2: the first live run's tool trace was truncated by a `tail` and
the quota to reproduce it was already gone.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage

from app.agent.graph import answer_question
from app.agent.model import build_chat_model, provider_for
from app.config import AGENT_TOOL_CAP, get_settings
from app.db.pool import close_pool, create_pool
from app.db.queries import resolve_repo_id

TRACE_DIR = Path(__file__).resolve().parents[2] / "var" / "traces"


def _slug(text: str, n: int = 40) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:n] or "question"


def write_trace(model: str, question: str, body: str) -> Path:
    """Persist a run's full trace; returns the path written."""
    TRACE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = TRACE_DIR / f"{stamp}-{_slug(model, 24)}-{_slug(question)}.txt"
    path.write_text(body, encoding="utf-8")
    return path


def _summarize(payload: str, limit: int = 160) -> str:
    """One-line gist of a tool result — full JSON is rarely readable in a trace."""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return payload[:limit]
    if "error" in data:
        return f"ERROR: {data['error']}"
    if "hits" in data:
        hits = data["hits"]
        head = ", ".join(f"{h['file_path']}:{h['start_line']}" for h in hits[:3])
        return f"{len(hits)} hits" + (f" — {head}" if head else "")
    if "matches" in data:
        return f"{len(data['matches'])} match(es): " + ", ".join(
            m["qualname"] for m in data["matches"][:3]
        )
    if "references" in data:
        return f"{len(data['references'])} reference(s)"
    if "symbols" in data:
        note = " (truncated)" if data.get("truncated") else ""
        return f"{len(data['edges'])} edge(s), {len(data['symbols'])} symbol(s){note}"
    if "content" in data:
        return f"{data['path']}:{data['start_line']}-{data['end_line']}"
    if "tree" in data:
        return f"{len(data['tree'].splitlines())} entries"
    return json.dumps(data)[:limit]


async def run(ref: str, question: str, *, as_json: bool, tool_cap: int) -> int:
    settings = get_settings()
    model_name = settings.AGENT_MODEL or "(unset)"
    pool = await create_pool(settings.DATABASE_URL)
    try:
        async with pool.acquire() as conn:
            repo_id = await resolve_repo_id(conn, ref)
            if repo_id is None:
                print(f"error: repo {ref!r} not ingested; run the ingest CLI --db")
                return 1

            if not as_json:
                print(f"model:    {model_name}  (provider={provider_for(model_name)})")
                print(f"question: {question}\n")

            model = build_chat_model()
            start = time.perf_counter()
            state = await answer_question(
                model, conn, repo_id, question, tool_cap=tool_cap
            )
            elapsed = time.perf_counter() - start

    finally:
        await close_pool(pool)

    answer = ""
    trace: list[dict[str, Any]] = []
    pending: dict[str, dict[str, Any]] = {}
    for m in state["messages"]:
        if isinstance(m, AIMessage):
            for call in m.tool_calls or []:
                pending[call.get("id") or ""] = {
                    "tool": call["name"],
                    "args": call["args"],
                }
            if not (m.tool_calls or []):
                text = m.text() if callable(getattr(m, "text", None)) else str(m.content)
                if text.strip():
                    answer = text
        elif isinstance(m, ToolMessage):
            entry = pending.pop(
                m.tool_call_id or "", {"tool": m.name, "args": {}}
            )
            entry["result"] = _summarize(str(m.content))
            trace.append(entry)

    payload_obj = {
        "model": model_name,
        "question": question,
        "answer": answer,
        "citations": state["citations"],
        "tool_calls_used": state["tool_calls_used"],
        "trace": trace,
        "elapsed_s": round(elapsed, 2),
    }

    lines = [
        f"model:    {model_name}",
        f"question: {question}",
        "",
    ]
    for i, entry in enumerate(trace, start=1):
        args = ", ".join(f"{k}={v!r}" for k, v in entry["args"].items())
        lines.append(f"  [{i}] {entry['tool']}({args})")
        lines.append(f"      -> {entry['result']}")
    lines += [
        "",
        "=" * 60,
        answer,
        "=" * 60,
        f"tool calls: {state['tool_calls_used']}/{tool_cap}   {elapsed:.1f}s",
        f"citations:  {len(state['citations'])} validated",
        *(
            f"  {c['file_path']}:{c['start_line']}-{c['end_line']}"
            for c in state["citations"]
        ),
    ]
    human = "\n".join(lines)

    # Always persist, whatever the output mode — see the module docstring.
    trace_path = write_trace(
        model_name, question, f"{human}\n\n{json.dumps(payload_obj, indent=2)}"
    )

    if as_json:
        print(json.dumps(payload_obj, indent=2))
    else:
        print(human)
        print(f"trace:      {trace_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.agent.cli")
    parser.add_argument("repo", help="repo URL or id (must already be ingested)")
    parser.add_argument("question")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--tool-cap", type=int, default=AGENT_TOOL_CAP)
    args = parser.parse_args(argv)
    return asyncio.run(
        run(args.repo, args.question, as_json=args.json, tool_cap=args.tool_cap)
    )


if __name__ == "__main__":
    sys.exit(main())
