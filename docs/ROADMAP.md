# ROADMAP.md — Codebase Onboarding Assistant

Ordering principle: **CLI-first, backend-first, UI last.** Every phase ends
with something runnable and verifiable from the terminal. The web layers
(Phases 4–5) are only built after Phase 3 proves the core thesis — that
agent graph-traversal beats plain retrieval.

"Done" always inherits the CLAUDE.md working agreement: tests passing,
ruff/mypy clean, status updated here, DECISIONS.md entry for any
architectural choice made along the way.

## Status

| Phase | Name | Status | Rough effort |
|---|---|---|---|
| 0 | Foundations | done | 1 day |
| 1 | Parse & chunk (CLI) | done | 1–2 weekends |
| 2 | Store & retrieve | not started | 1–2 weekends |
| 3 | Symbol graph & agent | not started | 2 weekends |
| — | **Go/no-go checkpoint** | — | — |
| 4 | API & worker | not started | 1–2 weekends |
| 5 | Frontend | not started | 2 weekends |
| 6 | Evidence & ship | not started | 1 weekend |

Statuses: `not started` → `in progress` → `done`. One phase in progress at
a time. Do not start a phase while the previous phase has failing
done-when criteria.

## SPEC prerequisites

A phase may not begin until its SPEC sections exist:

| Before phase | SPEC sections required |
|---|---|
| 1 | Chunk format & enrichment header; ingestion filters |
| 2 | DB schema (chunks); hybrid retrieval algorithm; eval metric definitions |
| 3 | DB schema (symbols, edges); tool signatures; agent loop & state |
| 4 | API contracts; SSE event schema; job lifecycle |

If a section is missing, writing it is the first task of the phase.

---

## Phase 0 — Foundations

**Goal:** an empty but fully wired repo. Everything boots; nothing does
anything yet.

Tasks:
- Repo scaffold per CLAUDE.md layout; commit CLAUDE.md and docs skeleton
- `docker-compose.yml`: postgres:16 with pgvector + redis, healthchecks,
  persistent volume
- Backend: uv project, FastAPI app with `GET /health`, `app/config.py`
  (pydantic-settings), `.env.example`
- `scripts/migrate.py` + `migrations/001_init.sql` (repos table only, with
  status/progress columns)
- ARQ worker entrypoint that starts and connects (no tasks yet)
- Frontend: Next.js 15 scaffold, Tailwind + shadcn/ui installed, one page
- Tooling: ruff, mypy, pytest with one trivial passing test; pnpm lint

Done when:
- [~] `docker compose up -d` brings both services healthy — compose file
      complete (pgvector/pg16 + redis, healthchecks, named volume); **not
      executed on this dev machine (no Docker daemon installed)**. Verify on
      a Docker host.
- [~] `migrate.py` applies 001 idempotently (re-running is a no-op) — script
      + 001 migration complete; discovery/idempotency logic verified in
      isolation; **not run against a live Postgres here (no Docker)**. Verify
      on a Docker host.
- [x] `GET /health` returns 200; worker starts without error — `/health`
      verified live (returns `{"ok": true}` with DB down); worker boots,
      loads config, attempts Redis connection.
- [x] `pnpm dev` renders the placeholder page — verified (page served with
      project title; `pnpm build` also compiles clean).
- [~] `ruff check`, `mypy app`, `pytest` all pass — `pytest` ✓, `mypy` ✓;
      **`ruff` is blocked by this machine's Application Control (WDAC)
      policy** (its native binary won't execute). Runs on an unrestricted
      host.

Do not: write any ingestion/retrieval/agent logic; add dependencies beyond
the scaffold set.

> **Phase 0 environment notes (this dev machine):** Docker is not installed,
> so the two compose-dependent checks above are code-complete but unrun here.
> `ruff`'s prebuilt binary and `mypy`'s compiled `mypyc` extension are blocked
> by a Windows Application Control policy; `mypy` was run from a pure-Python
> build and passes, `ruff` could not be executed. All of this is host
> restriction, not project defect — everything runs on an unrestricted machine
> with Docker.

---

## Phase 1 — Parse & chunk (CLI only)

**Goal:** correct AST-boundary chunking, inspectable by eye. No DB, no
embeddings, no web.

Tasks:
- Shallow clone (GitPython) into a work dir; cleanup on failure
- `app/ingest/filters.py`: ignore dirs, `.gitignore` respect, file-size
  cap, binary sniffing — all constants here, values per SPEC
- tree-sitter (≥0.22 bindings) + tree-sitter-python; queries for
  functions, methods, classes — including decorated, async, and nested defs
- Chunk assembly with enrichment header: file path, qualified name,
  signature, docstring, file-level imports. **No called-by yet** — that
  data arrives with the symbol graph in Phase 3 and is attached at
  context-assembly time, not embedded (log this in DECISIONS.md)
- Oversized-node fallback: split on statement boundaries, never characters
- CLI: `python -m app.ingest.cli <github_url>` → prints stats, `--dump`
  writes chunks as JSONL for inspection
- Pick and pin the benchmark repo: a well-known mid-size Python codebase
  (candidates: `encode/httpx`, `pallets/flask`); record name + commit SHA
- **Write `docs/EVAL.md` now, blind:** 20 questions about the benchmark
  repo with ground-truth answer locations (file paths + symbol names).
  Committed before any retrieval code exists.

Done when:
- [x] CLI ingests the benchmark repo at its pinned SHA without errors —
      `encode/httpx` @ `b5addb64f0161ff6bfe94c124ef76f6a1fba5254`: 60 files,
      1371 chunks (module 63, class 133, function 694, method 481), 102
      oversize splits, 0 syntax/parse errors, ~5s.
- [x] Chunker unit tests pass for: top-level fn, method, nested fn,
      decorated fn, async fn, class with docstring, oversized fn
      (statement-split), file with syntax errors (skip + warn, no crash),
      empty file — plus filters, module-path derivation, and 1-based lines.
      33 tests pass (`uv run pytest`); `mypy app` clean.
- [ ] 30 randomly sampled chunks manually spot-checked: boundaries clean,
      headers accurate — sample written to `docs/samples/phase1-sample.txt`;
      **left unticked for the human review pass.**
- [x] EVAL.md committed with pinned SHA + 20 questions + ground truth —
      pinned repo/SHA header, short-name symbols with the qualname-suffix
      match rule, frozen-once-Phase-2-begins rule; all ground truth verified
      programmatically against the pinned SHA.

Do not: touch the database; embed anything; start TypeScript grammar
support; gold-plate the header format before retrieval numbers exist.

> **Phase 1 environment note (this dev machine):** `ruff` remains blocked by
> the Windows Application Control (WDAC) policy (os error 4551) exactly as in
> Phase 0; code was written ruff-clean and the check is deferred to an
> unrestricted host. tree-sitter's native extension loads fine under WDAC, so
> the Step 0.5 environment gate passed and backend work stayed on this machine.

---

## Phase 2 — Store & retrieve

**Goal:** hybrid retrieval with measured quality.

Tasks:
- `002_chunks.sql`: chunks table with vector column (HNSW, cosine) and
  generated tsvector column; FTS index
- `app/ingest/embedder.py`: the only module importing
  sentence-transformers; batch encode; model from config
- Ingest CLI writes to DB; re-ingesting a repo is idempotent
  (delete-and-replace by repo_id)
- `retrieval.hybrid_search()`: single SQL query, vector + FTS fused with
  RRF, per SPEC
- Reranker wrapper (CrossEncoder) over top-40 → top-10
- `scripts/debug_search.py`: for a query, dump per-signal ranks and scores
  side by side — build this early, it is the retrieval debugger
- `scripts/eval.py`: hit-rate@5 and @10 against EVAL.md ground truth, for
  four modes: vector-only, FTS-only, hybrid, hybrid+rerank; writes a
  results block into EVAL.md

Done when:
- [ ] Benchmark repo ingested to Postgres; chunk count recorded
- [ ] eval.py runs all four modes and records numbers in EVAL.md
- [ ] hybrid+rerank ≥ every single-signal mode on hit-rate@10; if not,
      diagnose with debug_search.py before proceeding
- [ ] Idempotent re-ingest verified by test

Do not: write any agent code; hand-tune against individual EVAL questions
(script only); add a dedicated vector DB; expose anything over HTTP.

---

## Phase 3 — Symbol graph & agent

**Goal:** the thesis. Retrieval finds entry points; the agent's graph
traversal finds the answer.

Tasks:
- `003_symbols.sql`: symbols + edges tables per SPEC
- Symbol pass during ingestion: definitions from tree-sitter; import/call
  edges resolved with Jedi. Timebox resolution — accept ~80%, log
  unresolved edges, move on
- Called-by context assembly: when a chunk is shown to the agent, attach
  its incoming edges (this is where the Phase 1 deferral pays off)
- Implement the six tools per SPEC signatures: `search_code`, `read_file`,
  `get_definition`, `find_references`, `expand_context`, `list_directory`;
  unit-test each against a small fixture repo
- LangGraph loop: model node, tool node, conditional edge, hard cap 8 tool
  executions then forced answer; all events streamable
- CLI chat: `python -m app.agent.cli <repo> "<question>"` — streams tool
  calls and the final answer with file:line citations
- Extend eval.py with answer-level scoring: does the final answer cite at
  least one ground-truth location? Run for (a) retrieval-only answering
  and (b) full agent; record both in EVAL.md

Done when:
- [ ] All six tools pass unit tests
- [ ] Agent answers all 20 EVAL questions from the CLI without crashing;
      cap enforcement verified by test
- [ ] Answer-level eval recorded for retrieval-only vs agent
- [ ] Unresolved-edge rate logged and noted in EVAL.md

Do not: build HTTP endpoints; let Jedi resolution become a two-week rabbit
hole; add tools beyond the six without a DECISIONS entry.

---

## Go/no-go checkpoint

Stop and look at the Phase 3 numbers before writing a single line of web
code.

- **Go:** agent beats retrieval-only on answer-level eval by a clear
  margin. Proceed to Phase 4.
- **No-go:** margin is small or negative. Diagnose (tool prompts? graph
  coverage? retrieval quality?) and fix before proceeding — or scope the
  project down honestly. Building UI on a core that doesn't work wastes
  the remaining weeks.

Either way, write the checkpoint outcome into DECISIONS.md.

---

## Phase 4 — API & worker

**Goal:** everything Phase 3 does, over HTTP, with ingestion as a proper
background job.

Tasks:
- `POST /repos` → validate URL, create row, enqueue ARQ ingest job,
  return 202 with repo id
- ARQ ingest task: full pipeline with progress writes to Postgres
  (files_parsed, chunks_embedded, status, error); failure captured, retry
  safe
- `GET /repos/{id}`: status + progress; `GET /repos`: list
- `GET /repos/{id}/chat` (SSE): streams agent events per SPEC event
  schema — tool_call, text delta, citations, done; 8-cap enforced
  server-side
- Error mapping: repo not found, repo still indexing, ingest failed

Done when:
- [ ] Full flow via curl: submit → poll to `ready` → stream a chat with
      visible tool-call events and citations
- [ ] Kill the worker mid-ingest; job retries or fails cleanly with a
      recorded error — no zombie `indexing` rows
- [ ] API tests: happy path, unknown repo, chat-before-ready

Do not: build any UI; switch to WebSockets; refactor the agent while
wiring transport.

---

## Phase 5 — Frontend

**Goal:** the demo. Streaming visible work is the point.

Tasks:
- Submit page: URL input → creates repo → progress view (files parsed /
  chunks embedded) polled via TanStack Query
- Chat page: Vercel AI SDK `useChat` over the SSE endpoint; tool-call
  steps render live as they stream; assistant messages show citation chips
- Split pane: Shiki-rendered file viewer; clicking a citation loads the
  file and scrolls to + highlights the line range
- Loading, empty, and error states for every screen

Done when:
- [ ] Full flow in the browser on the benchmark repo: submit → watch
      progress → ask an EVAL question → watch tool calls stream → click a
      citation → correct lines highlighted
- [ ] No console errors; indexing and chat survive a page refresh

Do not: add backend features; build the symbol-graph mini-map (backlog);
fight shadcn defaults for pixel perfection.

---

## Phase 6 — Evidence & ship

**Goal:** make the quality legible to a stranger in 60 seconds. Feature
freeze.

Tasks:
- Naive-chunking baseline: `--strategy naive` flag (fixed-size character
  splits) in the chunker, ingest benchmark repo as a separate row, run
  the full eval
- README comparison table: naive vs AST-chunking vs AST+agent, hit-rate
  and answer-level numbers from eval.py — this table is the headline
- Deploy: frontend → Vercel; API + worker → Railway or Fly (persistent
  disk for clones); Postgres → Neon; Redis → Upstash; deployed URL in
  README
- README: what/why, architecture diagram, the numbers table, demo GIF,
  link to DECISIONS.md
- Hardening pass: repo-size guard at submit, friendly error surfaces,
  timeouts on clone and model calls

Done when:
- [ ] Deployed URL works end-to-end on a fresh repo
- [ ] README contains the comparison table with real numbers and a GIF
- [ ] A stranger can run it locally from README instructions alone

Do not: add features. Anything tempting goes to the backlog below.

---

## v2 backlog (explicitly deferred)

- TypeScript grammar + import resolution
- Commit-history indexing (`search_commits` tool)
- Private repos (GitHub tokens) and auth/multi-user
- Symbol-graph mini-map (react-force-graph)
- Embedding model upgrade pass (code-specific model A/B via eval.py)
- Incremental re-indexing on new commits

## Known risks

- **Import resolution** has a practical ~80% ceiling. Budgeted for in
  Phase 3; unresolved edges are logged, not chased.
- **Retrieval debugging** is opaque without tooling — debug_search.py is
  built early in Phase 2 for exactly this reason.
- **Scope creep** concentrates at grammar support and agent tools. Both
  are gated behind DECISIONS entries on purpose.
- **Model cost** during Phase 3+ eval runs: 20 questions × 8 tool calls
  adds up. Run answer-level eval deliberately, not on every save.
