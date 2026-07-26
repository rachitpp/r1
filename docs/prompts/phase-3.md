# Phase 3 prompt — Symbol graph & agent (FINAL, consolidated)

> **How to use:** save as `docs/prompts/phase-3.md`, replacing any
> earlier draft — this version supersedes the prior prompt and the two
> chat amendments (Gemini provider swap; conditional Vertex branch).
> Start a fresh Claude Code session at the repo root (Opus recommended)
> and instruct: "Read docs/prompts/phase-3.md completely, confirm
> Phase 2 is done in ROADMAP.md, give me a ≤12-line plan, then
> proceed." This phase runs in **milestones (M1→M3) with a report back
> to the human at each** — it is expected to span multiple sessions.
> Resume via ROADMAP + HANDOFF.

---

You are starting **Phase 3 — Symbol graph & agent**: the thesis phase.
Retrieval finds entry points; graph traversal finds the answer. Build
the graph, the six tools, and the LangGraph loop — then measure whether
the agent beats retrieval-stuffing at the answer level.

## Step 0 — Orient

Read, in order:
1. `CLAUDE.md`
2. `docs/ROADMAP.md` — Phase 3 section AND the go/no-go checkpoint
3. `docs/SPEC.md` — §3, §6, §7 (all), §11.2, §12
4. `docs/DECISIONS.md` — every Phase 2 entry (reranker ablation,
   is_test, dormant injection, gate amendment)
5. `docs/HANDOFF.md` — the "Phase 2 outcome" section

Confirm Phase 2 is `done`. Plan in ≤12 lines, then proceed.

## Step 0.5 — Gates

**Gate A — provider + model smoke test.** The agent model is
provider-configurable via `AGENT_MODEL` (a cost decision: default is
Gemini's free tier; the Anthropic path stays wired for the
pre-registered cross-check).

```bash
cd backend
uv add langgraph langchain-google-genai langchain-anthropic jedi
```

Then run an inline smoke test against whatever `AGENT_MODEL` resolves
to (read it from config): prefix `gemini` → `ChatGoogleGenerativeAI`
with `GOOGLE_API_KEY`; prefix `claude` → `ChatAnthropic` with
`ANTHROPIC_API_KEY`; prefix `vertex:` → see the Vertex branch below.
`max_tokens=32`, prompt "Say OK".

- Missing key / auth failure / invalid model id → **STOP** and tell the
  human what to set in `backend/.env`. No mocking past this gate.
- A **429** on the smoke call → **PASS with warning** (auth works;
  we're rate-limited). Note it and continue.

**Vertex branch (conditional — env-driven, do not ask repeatedly):**
check whether `backend/.env` defines `GOOGLE_APPLICATION_CREDENTIALS`
and `GCP_PROJECT`. If **yes** (the human confirmed live GCP trial
credits): additionally `uv add langchain-google-vertexai`, add optional
config fields `GCP_PROJECT` / `GCP_LOCATION`, and wire the `vertex:`
prefix (e.g. `vertex:gemini-3-pro`) → `ChatVertexAI`. If **no**:
implement the `vertex:` branch as a stub that raises with a one-line
setup instruction, and move on — do not set up Vertex, do not block.

**Gate B — imports.** `jedi` and `langgraph` import cleanly.

## Session rules

- Build **only Phase 3**: no HTTP endpoints, no SSE, no frontend.
- Pre-authorized deps: `langgraph`, `langchain-google-genai`,
  `langchain-anthropic`, `jedi` (+ `langchain-google-vertexai` only if
  the Vertex condition above is met). Nothing else without asking.
- **Model factory:** `app/agent/model.py` is the single place a chat
  model is constructed — prefix-dispatched (`gemini` / `claude` /
  `vertex:`), retries with exponential backoff configured on the
  client. Everything else imports the factory. Tool binding stays
  provider-agnostic (`.bind_tools` on whatever the factory returns).
- `search_code` consumes the **default** retrieval pipeline. Never pass
  `rerank=True`, never re-enable injection (DECISIONS).
- **Benchmark contamination guard (hard rule):** the frozen 20 EVAL
  questions are for *measurement only*. All iterative tuning — prompt
  wording, tool descriptions, strategy — happens against self-authored
  **dev questions** (M3 step 12). Full frozen-set runs are deliberate,
  counted, and logged with the model id. q10 and the flow tier may be
  *observed* in measurement runs; every fix stays generic — no
  question-specific hacks, ever.
- **Rate-limit + cost control (free tier is the budget):** Gemini free
  tier is roughly 10–15 RPM with daily caps — a full 20-question
  both-mode run is ~200 calls and must be paced. Tune on single dev
  questions; the answer-level eval gets `--questions`, `--limit`, and
  `--pace <seconds>` (default ~6s between questions when the provider
  is Gemini); handle 429s with backoff, never tight retries; report
  call counts per run; record `AGENT_MODEL` in every results block. A
  full frozen-set run is a deliberate act, not a reflex.
- ruff + mypy green per commit; small logical commits.

## Reconciliations (do early, log each in DECISIONS.md)

1. **Migration renumber:** symbols/edges land in `004_symbols.sql`
   (003 is `is_test`). The `chunks.symbol_id` backfill column moves
   there too. Update SPEC §3's migration list to match.
2. **Test symbols — flag-and-filter, consistent with Phase 2:** extract
   symbols and edges from ALL files (tests included), add
   `symbols.is_test BOOLEAN NOT NULL` (from the file's Phase 2
   classification). Tool defaults exclude the test side:
   `get_definition` skips test-file definitions; `find_references` and
   `expand_context(direction="in")` exclude edges whose *from*-side
   symbol is a test; outward expansion is unaffected in practice.
   `include_tests: bool = False` on `get_definition` and
   `find_references`, mirroring `search_code`.
3. **§7.4 called-by filter:** the called-by comment block draws from
   implementation-side incoming edges only, capped at 8 callers with
   `+N more`.
4. **Provider-configurable agent model:** DECISIONS entry — rationale
   (zero marginal cost; `AGENT_MODEL` was env config by design since
   Phase 0); measurement rules — model id recorded in every results
   block, stuffed-vs-agent comparisons are *within-model only*, and a
   one-time strong-model cross-check (Claude Sonnet, or `vertex:`
   Pro-class while credits last) is the pre-registered diagnostic if
   the M3 delta is ambiguous; note the AI Studio free-tier
   training-data clause (payload is public repo code only). If the
   Vertex branch is live, add its usage policy: tuning stays on the
   free AI Studio key; Vertex Pro-class is for measurement runs and
   the cross-check only; default traffic never routes through Vertex.
   Touch CLAUDE.md's stack row and SPEC §7.2's model line to say
   "provider-configurable via AGENT_MODEL (Gemini / Claude / Vertex)".

---

## M1 — The graph

1. `004_symbols.sql`: `symbols` + `edges` exactly per SPEC §3, plus
   `symbols.is_test` and `ALTER TABLE chunks ADD COLUMN symbol_id
   BIGINT REFERENCES symbols(id)`.
2. `app/ingest/symbols.py`:
   - Definitions from the existing tree-sitter pass (same nodes as
     chunking) → `symbols` rows.
   - Call sites and class bases located via tree-sitter; resolved via
     Jedi (`jedi.Project(workdir)`, `Script.goto` at each site) →
     `edges` with kind `imports | calls | extends`. Drop targets
     outside the repo.
   - Best-effort per-file timebox `JEDI_FILE_TIMEOUT_S`: wall-clock
     check between resolutions; on budget blown, skip the file's
     remaining edges and log. No hard interrupts. ~20% unresolved
     overall is acceptable by design — log the rate, move on.
3. Wire into the ingest job **after parsing, while the clone is still
   on disk** (Jedi needs real files; the workdir is deleted at job
   end). Backfill `chunks.symbol_id`. Re-ingest httpx with `--db`.
4. Tests: extend the Phase 2 fixture repo with cross-file imports, a
   cross-file call, and cross-file inheritance; assert expected edges
   resolve. Integration test re-runs ingest; symbol/edge counts stable.

**REPORT M1 before continuing:** symbol and edge counts by kind,
implementation/test split, unresolved-edge rate by kind, symbol-pass
timing (HANDOFF baseline: ingest ≈7 min before this pass).

---

## M2 — Tools and loop

5. `app/agent/tools.py` — the six tools, **signatures exactly per SPEC
   §7.1**, error-dict pattern (`{"error": ...}`, never raise into the
   loop):
   - `search_code` wraps `retrieval.hybrid_search` (default pipeline).
   - `read_file` line-numbered; whole-file only if ≤ `READ_MAX_LINES`,
     else an error instructing a range.
   - `get_definition` ≤5 matches, code included,
     name-or-qualname-suffix match.
   - `find_references` incoming edges with kind filter.
   - `expand_context` BFS over edges, depth clamped to
     `EXPAND_MAX_DEPTH`, direction out|in|both, total code capped at
     `EXPAND_TOKEN_BUDGET` via the embedder's `token_len` (already
     resident for query embedding), breadth-first truncation with a
     `truncated: true` flag.
   - `list_directory` 2-level tree from the `files` table.
6. §7.4 context assembly: helper appending the called-by comment block
   whenever a symbol/chunk body enters a tool result.
7. `app/agent/model.py` — the provider factory (see Session rules).
8. `app/agent/graph.py` — LangGraph per SPEC §7.2: `AgentState`; model
   node (factory-built model, tools bound); tool node; conditional
   edge; at `AGENT_TOOL_CAP` (8) inject the forced-answer message and
   make one final call with tools disabled. Streamable via
   astream_events v2 or the current equivalent — keep the §7.2 design,
   use the current API.
9. `app/agent/prompts.py` — system prompt per the §7.3 outline: role
   with injected repo facts (name, file count, top-level dirs);
   strategy (search for entry points → traverse with
   expand_context/get_definition rather than re-searching → read_file
   for precision); citation contract (`[path:start-end]` inline, no
   uncited claims about code, say plainly when something isn't found).
10. Citation parser utility (shared with Phase 4 later): regex
    `[path:start-end]`, validated against the `files` table.
11. Tests: loop mechanics with a **scripted fake model** (no network) —
    tool dispatch, cap enforcement, forced final answer, state
    accumulation. Six tools unit-tested against the fixture repo.
    Citation parser edge cases.
12. `app/agent/cli.py`: `python -m app.agent.cli <repo> "question"` —
    streams tool calls and the final answer with citations; `--json`
    for machine-readable output.

**REPORT M2:** run exactly ONE self-authored dev question end-to-end
live and paste the full trace (tool calls, result summaries, answer).

---

## M3 — Tune, then measure

13. Author **≥6 dev questions** about httpx, distinct from the frozen
    20, spanning locate, conceptual, flow, and at least one
    identifier-dense query (exercises `get_definition`, which replaced
    injection). Record them, dated, in the review doc. **All tuning
    iterations run against these only.** Log each iteration (what
    changed, why, observed effect) in the review doc.
14. Extend `scripts/eval.py` with answer-level modes per SPEC §11.2:
    - `stuffed`: ONE model call, top-10 default-pipeline chunks in
      context, **the same citation contract in its prompt** (fairness —
      it must not lose on format), no tools.
    - `agent`: the full loop.
    - Metric: **answer-hit** — final answer contains ≥1 parsed citation
      whose file ∈ truth.files. Also record: citations-present rate,
      tool_calls_used distribution, per-question hit/miss table.
    - Flags: `--questions`, `--limit`, `--pace`. Results block appended
      to EVAL.md with date + model id; old blocks untouched.
15. If measuring on a **different model** than the one tuned on (e.g.
    tuned on Flash, measuring on a Pro-class or Claude model): run one
    dev-question sanity pass on the measurement model first.
16. **Measurement run:** the full frozen 20, both modes, same model,
    paced for the provider's rate limits.

**REPORT M3 and PAUSE.** Present: the stuffed-vs-agent table, the
per-question grid (flag q10 and q16–q20 explicitly), tool-call stats,
call counts and model id. **The go/no-go checkpoint is a human ruling.
Do not self-declare pass/fail, do not proceed to wrap-up, do not begin
any Phase 4 work.**

## What success looks like (context, not a target to force)

The stuffed baseline will likely score well on the locate tier — a
single chunk answers those. The thesis lives in the *delta*: flow
questions (q16–q20) and q10 need code assembled across files, which is
what expand_context exists for. A large agent win on that tier with
rough parity elsewhere proves the thesis. If the agent *loses*
anywhere, that is diagnostic gold — report it straight. If the overall
delta is ambiguous, the pre-registered next step is the strong-model
cross-check — not a redesign.

## Verification (per milestone)

`uv run pytest` · `uv run ruff check .` · `uv run mypy app` — green at
every milestone. M1 adds ingest stats; M2 adds the fake-model loop
tests plus one live trace; M3 adds the eval tables.

## Wrap-up — ONLY after the human's checkpoint ruling

ROADMAP Phase 3 boxes + status per the ruling; DECISIONS entries
(reconciliations, provider decision, tuning protocol, checkpoint
outcome); HANDOFF updated (graph stats, agent state, eval numbers,
ruling); final commit; ≤10-line summary.
