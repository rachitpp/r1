"""Answer-level evaluation: stuffed baseline vs the agent loop (SPEC §11.2).

`eval.py` measures *retrieval* — did the right chunk make the top-k. This
measures *answers* — did the final response cite the right file. They are
separate scripts because they are separate questions, and because this one
spends model calls while that one does not.

    uv run python scripts/answer_eval.py --mode both
    uv run python scripts/answer_eval.py --mode both --dev            # tuning set
    uv run python scripts/answer_eval.py --mode agent --questions q10,q16
    uv run python scripts/answer_eval.py --mode both --limit 3 --pace 1.0

**Tuning runs use ``--dev``.** The frozen 20 are for deliberate, counted
measurement runs only (CLAUDE.md; Phase 3 contamination guard) — a prompt
iterated against them stops measuring anything.

Two metrics, deliberately:

**answer-hit** (file-level) — ≥1 validated citation whose file ∈ ``truth.files``.
**symbol-hit** (symbol-level) — the same, *and* the answer demonstrably uses a
truth symbol by name.

The file-level metric is largely **retrieval-bound**: the stuffed baseline is
handed a top-10 pool whose hit@10 is 0.95, so "the right file was in the
context window" and "the model assembled an answer" score identically. Only a
question retrieval cannot reach has discriminating power under it — on the
frozen 20, exactly one (q10). The symbol-level metric asks for something a
pool cannot supply by accident: that the answer names the specific construct
the question is about. It is the criterion that can separate assembly from
retrieval on the other 19.

Also recorded: citations-present rate, the tool-call distribution, and whether
each answer used a **graph tool** (expand_context / get_definition /
find_references) before answering — cross-tabulated against correctness, which
turns the tool-mix observation into a table rather than an anecdote.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

import asyncpg
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agent.citations import parse_citations, validate_citations  # noqa: E402
from app.agent.graph import answer_question, repo_facts  # noqa: E402
from app.agent.model import build_chat_model, provider_for  # noqa: E402
from app.config import AGENT_TOOL_CAP, SEARCH_K, get_settings  # noqa: E402
from app.db.pool import close_pool, create_pool  # noqa: E402
from app.db.queries import resolve_repo_id  # noqa: E402
from app.retrieval.hybrid import search  # noqa: E402

DOCS = Path(__file__).resolve().parent.parent.parent / "docs"
EVAL_MD = DOCS / "EVAL.md"
DEV_QUESTIONS = DOCS / "dev-questions.yaml"

AnswerMode = str  # "stuffed" | "agent"

# Tools that consult the symbol graph rather than the retrieval index. The
# thesis is that these reach code search missed, so their use is the mechanism
# under test — tracked per question, not just in aggregate.
GRAPH_TOOLS = frozenset({"expand_context", "get_definition", "find_references"})
ANSWER_MODES: tuple[str, ...] = ("stuffed", "agent")

# Mistral's free tier is 1 RPS. Within a run the agent's calls are sequential
# anyway; this paces the gap *between questions*.
DEFAULT_PACE_S = 1.5


# ---------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------


def load_frozen() -> tuple[str, list[dict]]:
    """The frozen 20 from EVAL.md — measurement only."""
    text = EVAL_MD.read_text(encoding="utf-8")
    m = re.search(r"```yaml\n(.*?)\n```", text, re.DOTALL)
    if m is None:
        raise SystemExit("EVAL.md: no ```yaml question block found")
    url_m = re.search(r"\((https://github\.com/[^)]+)\)", text)
    questions = yaml.safe_load(m.group(1))
    return (url_m.group(1) if url_m else ""), questions


def load_dev() -> tuple[str, list[dict]]:
    """The dev set — everything tuning is allowed to touch."""
    if not DEV_QUESTIONS.exists():
        raise SystemExit(f"missing {DEV_QUESTIONS}")
    return "", yaml.safe_load(DEV_QUESTIONS.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass
class QuestionResult:
    qid: str
    mode: str
    hit: bool
    cited: bool
    n_citations: int
    tool_calls: int
    elapsed_s: float
    tools_used: list[str] = field(default_factory=list)
    symbol_hit: bool = False
    error: str | None = None

    @property
    def used_graph_tool(self) -> bool:
        return any(t in GRAPH_TOOLS for t in self.tools_used)


@dataclass
class ModeResult:
    mode: str
    rows: list[QuestionResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.rows)

    @property
    def hits(self) -> int:
        return sum(1 for r in self.rows if r.hit)

    @property
    def cited(self) -> int:
        return sum(1 for r in self.rows if r.cited)

    @property
    def symbol_hits(self) -> int:
        return sum(1 for r in self.rows if r.symbol_hit)

    @property
    def errors(self) -> int:
        return sum(1 for r in self.rows if r.error)

    @property
    def tool_calls(self) -> list[int]:
        return [r.tool_calls for r in self.rows if r.error is None]

    def rate(self, n: int) -> str:
        return f"{n / self.total:.2f} ({n}/{self.total})" if self.total else "—"


# ---------------------------------------------------------------------------
# The two modes
# ---------------------------------------------------------------------------

STUFFED_SYSTEM = """\
You are a codebase onboarding assistant. Answer the question using ONLY the \
code excerpts provided below. Do not speculate beyond them.

Citations — this is a hard requirement, and the format is exact:

    [path:START-END]

CORRECT:   The Timeout class is defined in [httpx/_config.py:72-156].
INCORRECT: The Timeout class is defined in [httpx/_config.py:90,101].

A citation is one contiguous line RANGE with a hyphen, never a comma-separated
list and never a single line. Cite every claim about the code, inline, using
the line numbers shown with each excerpt. An answer about code with no
citation is wrong even when the prose is right.
"""


def uses_symbol(answer: str, symbols: list[str]) -> bool:
    """Whether ``answer`` demonstrably names one of ``symbols``.

    Word-boundary matched so ``URL`` does not match inside ``URLPattern`` and
    ``get`` does not match inside ``target``. A dotted qualname counts if its
    final segment appears — the answer saying ``TextDecoder`` is evidence
    whether or not it wrote ``httpx._decoders.TextDecoder``.

    This is deliberately a *use* test, not a citation test: the point is that
    the answer engages with the construct, which a top-10 pool cannot supply
    by accident the way it supplies a filename.
    """
    if not symbols:
        return False
    for sym in symbols:
        short = sym.rsplit(".", 1)[-1]
        if re.search(rf"\b{re.escape(short)}\b", answer):
            return True
    return False


async def run_stuffed(
    model: object, conn: asyncpg.Connection, repo_id: UUID, question: str
) -> tuple[str, int]:
    """One model call with the top-10 default-pipeline chunks in context.

    The baseline the thesis is measured against, so it gets a fair shake: the
    *same* retrieval the agent's first `search_code` would do, and the *same*
    citation contract in its prompt. If it loses, it must not lose on format.
    """
    hits = await search(conn, repo_id, question, k=SEARCH_K, mode="hybrid")
    blocks = []
    for h in hits:
        row = await conn.fetchrow("SELECT code FROM chunks WHERE id = $1", h["chunk_id"])
        code = str(row["code"]) if row else ""
        numbered = "\n".join(
            f"{h['start_line'] + i:>6}  {ln}" for i, ln in enumerate(code.splitlines())
        )
        blocks.append(
            f"--- {h['file_path']}:{h['start_line']}-{h['end_line']} "
            f"({h['symbol']}) ---\n{numbered}"
        )
    from langchain_core.messages import HumanMessage, SystemMessage

    messages = [
        SystemMessage(content=STUFFED_SYSTEM),
        HumanMessage(content="\n\n".join(blocks) + f"\n\nQuestion: {question}"),
    ]
    resp = await model.ainvoke(messages)  # type: ignore[attr-defined]
    text = resp.text if isinstance(resp.text, str) else str(resp.content)
    return text, 0


async def run_agent(
    model: object, conn: asyncpg.Connection, repo_id: UUID, question: str, cap: int
) -> tuple[str, int, list[str]]:
    """The full loop. Also returns which tools were called, in order.

    Tool *names* are the thesis diagnostic: an agent that only ever calls
    `search_code` and `read_file` is doing retrieval with extra steps. The
    claim is that graph traversal reaches code search missed, so
    `expand_context` / `find_references` usage is what distinguishes the two.
    """
    state = await answer_question(model, conn, repo_id, question, tool_cap=cap)  # type: ignore[arg-type]
    from langchain_core.messages import AIMessage

    answer = ""
    for m in reversed(state["messages"]):
        if isinstance(m, AIMessage) and not (getattr(m, "tool_calls", None) or []):
            answer = m.text if isinstance(m.text, str) else str(m.content)
            break
    used = [
        c["name"]
        for m in state["messages"]
        if isinstance(m, AIMessage)
        for c in (getattr(m, "tool_calls", None) or [])
    ]
    return answer, state["tool_calls_used"], used


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


async def evaluate(
    modes: list[str],
    questions: list[dict],
    *,
    repo_ref: str,
    pace: float,
    cap: int,
) -> tuple[list[ModeResult], str, int]:
    settings = get_settings()
    model_name = settings.AGENT_MODEL or "(unset)"
    pool = await create_pool(settings.DATABASE_URL)
    n_calls = 0
    results = [ModeResult(mode=m) for m in modes]
    try:
        async with pool.acquire() as conn:
            repo_id = await resolve_repo_id(conn, repo_ref)
            if repo_id is None:
                raise SystemExit(f"repo {repo_ref!r} not ingested")
            name, _, _ = await repo_facts(conn, repo_id)
            print(f"repo {name}   model {model_name}   {len(questions)} questions")

            model = build_chat_model()
            for mr in results:
                print(f"\n--- {mr.mode} ---")
                for q in questions:
                    truth = set(q.get("truth", {}).get("files", []))
                    t0 = time.perf_counter()
                    err: str | None = None
                    answer, used = "", 0
                    names: list[str] = []
                    try:
                        if mr.mode == "stuffed":
                            answer, used = await run_stuffed(
                                model, conn, repo_id, q["question"]
                            )
                            n_calls += 1
                        else:
                            answer, used, names = await run_agent(
                                model, conn, repo_id, q["question"], cap
                            )
                            n_calls += used + 1
                    except Exception as exc:  # noqa: BLE001 — one failure is a row
                        err = f"{type(exc).__name__}: {str(exc)[:120]}"

                    cites = await validate_citations(
                        conn, repo_id, parse_citations(answer)
                    )
                    hit = any(c["file_path"] in truth for c in cites)
                    sym_hit = hit and uses_symbol(
                        answer, q.get("truth", {}).get("symbols", []) or []
                    )
                    mr.rows.append(
                        QuestionResult(
                            qid=q["id"],
                            mode=mr.mode,
                            hit=hit,
                            cited=bool(cites),
                            n_citations=len(cites),
                            tool_calls=used,
                            elapsed_s=round(time.perf_counter() - t0, 1),
                            tools_used=names,
                            symbol_hit=sym_hit,
                            error=err,
                        )
                    )
                    mark = (
                        "ERR"
                        if err
                        else ("HIT+S" if sym_hit else ("HIT  " if hit else " .   "))
                    )
                    print(
                        f"  {q['id']}  {mark}  cites={len(cites):<2} "
                        f"tools={used}  {time.perf_counter() - t0:.0f}s"
                        + (f"  {err}" if err else "")
                    )
                    await asyncio.sleep(pace)
    finally:
        await close_pool(pool)
    return results, model_name, n_calls


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def summary_table(results: list[ModeResult]) -> list[str]:
    lines = [
        "| Mode | answer-hit (file) | symbol-hit | cited | tool calls (mean/max) | errors |",
        "|---|---|---|---|---|---|",
    ]
    for mr in results:
        tc = mr.tool_calls
        stat = f"{sum(tc) / len(tc):.1f} / {max(tc)}" if tc else "—"
        lines.append(
            f"| {mr.mode} | {mr.rate(mr.hits)} | {mr.rate(mr.symbol_hits)} | "
            f"{mr.rate(mr.cited)} | {stat} | {mr.errors} |"
        )
    return lines


def graph_tool_table(results: list[ModeResult]) -> list[str]:
    """Graph-tool use cross-tabulated against correctness (agent mode only).

    n is small, so this is a table rather than a claim — but it is the
    mechanism the thesis names, and an anecdote about tool mix is worth less
    than a 2x2 someone can argue with.
    """
    agent = next((mr for mr in results if mr.mode == "agent"), None)
    if agent is None or not agent.rows:
        return []
    cells = {(True, True): 0, (True, False): 0, (False, True): 0, (False, False): 0}
    for r in agent.rows:
        if r.error:
            continue
        cells[(r.used_graph_tool, r.symbol_hit)] += 1
    return [
        "| Agent run | symbol-hit | symbol-miss |",
        "|---|---|---|",
        f"| used a graph tool | {cells[(True, True)]} | {cells[(True, False)]} |",
        f"| no graph tool | {cells[(False, True)]} | {cells[(False, False)]} |",
    ]


def tool_usage_table(results: list[ModeResult]) -> list[str]:
    """Per-tool call counts — the thesis diagnostic (see run_agent)."""
    agent = next((mr for mr in results if mr.mode == "agent"), None)
    if agent is None:
        return []
    counts: dict[str, int] = {}
    for r in agent.rows:
        for name in r.tools_used:
            counts[name] = counts.get(name, 0) + 1
    if not counts:
        return []
    total = sum(counts.values())
    lines = ["| Tool | calls | share |", "|---|---|---|"]
    for name, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        lines.append(f"| {name} | {n} | {n / total:.0%} |")
    return lines


def grid_table(results: list[ModeResult]) -> list[str]:
    qids = [r.qid for r in results[0].rows]
    lines = [
        "| q | " + " | ".join(mr.mode for mr in results) + " |",
        "|---|" + "|".join("---" for _ in results) + "|",
    ]
    for i, qid in enumerate(qids):
        cells = []
        for mr in results:
            r = mr.rows[i]
            if r.error:
                cells.append("ERR")
            elif r.symbol_hit:
                cells.append("✓s")   # file AND symbol
            elif r.hit:
                cells.append("✓")    # file only
            else:
                cells.append("·")
        lines.append(f"| {qid} | " + " | ".join(cells) + " |")
    lines.append("")
    lines.append("`✓s` = file + symbol · `✓` = file only · `·` = miss")
    return lines


def append_block(
    results: list[ModeResult], model_name: str, n_calls: int, label: str
) -> None:
    block = [
        "",
        f"### Answer-level results — {dt.date.today().isoformat()}",
        "",
        f"**Model:** `{model_name}` · **Set:** {label} · "
        f"**Model calls:** {n_calls} · Metric: answer-hit (≥1 validated "
        f"citation whose file ∈ truth.files).",
        "",
        *summary_table(results),
        "",
        *(["Agent tool usage:", "", *tool_usage_table(results), ""]
          if tool_usage_table(results) else []),
        *(["Graph-tool use vs correctness (agent):", "",
           *graph_tool_table(results), ""]
          if graph_tool_table(results) else []),
        "Per-question:",
        "",
        *grid_table(results),
        "",
    ]
    with EVAL_MD.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(block) + "\n")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="scripts/answer_eval.py")
    p.add_argument("--mode", default="both", help="stuffed | agent | both")
    p.add_argument("--dev", action="store_true", help="use the dev set, not the frozen 20")
    p.add_argument("--questions", default="", help="comma-separated question ids")
    p.add_argument("--limit", type=int, default=0, help="first N questions only")
    p.add_argument("--pace", type=float, default=DEFAULT_PACE_S, help="seconds between questions")
    p.add_argument("--tool-cap", type=int, default=AGENT_TOOL_CAP)
    p.add_argument("--repo", default="", help="repo url or id")
    p.add_argument("--no-append", action="store_true", help="do not write to EVAL.md")
    args = p.parse_args(argv)

    modes = list(ANSWER_MODES) if args.mode == "both" else [args.mode]
    for m in modes:
        if m not in ANSWER_MODES:
            p.error(f"unknown mode {m!r}; valid: {list(ANSWER_MODES)} or 'both'")

    url, questions = load_dev() if args.dev else load_frozen()
    if args.questions:
        want = {q.strip() for q in args.questions.split(",") if q.strip()}
        questions = [q for q in questions if q["id"] in want]
    if args.limit:
        questions = questions[: args.limit]
    if not questions:
        p.error("no questions selected")

    repo_ref = args.repo or url or "https://github.com/encode/httpx"
    label = "dev (tuning)" if args.dev else "frozen 20 (measurement)"
    print(f"{label}: {len(questions)} question(s), modes={modes}, pace={args.pace}s")

    results, model_name, n_calls = asyncio.run(
        evaluate(modes, questions, repo_ref=repo_ref, pace=args.pace, cap=args.tool_cap)
    )

    print("\n" + "\n".join(summary_table(results)))
    usage = tool_usage_table(results)
    if usage:
        print()
        print("\n".join(usage))
    xtab = graph_tool_table(results)
    if xtab:
        print()
        print("\n".join(xtab))
    print()
    print("\n".join(grid_table(results)))
    print(f"\nmodel calls this run: {n_calls}   provider: {provider_for(model_name)}")

    if args.dev:
        print("dev set — not appended to EVAL.md (tuning runs are not results)")
    elif args.no_append:
        print("--no-append: not written to EVAL.md")
    else:
        append_block(results, model_name, n_calls, label)
        print(f"appended results block to {EVAL_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
