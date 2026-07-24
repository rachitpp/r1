# HANDOFF.md — project state

**Last updated:** 2026-07-24 · **Current position:** Phase 1 done,
Phase 2 blocked on environment, resuming on an unrestricted machine.

Read this first when picking the project up on a new machine or after a
break. Then read `CLAUDE.md`, then `docs/ROADMAP.md`.

## What this project is

A codebase onboarding assistant. Submit a GitHub repo URL → it clones,
chunks the code on AST boundaries (tree-sitter), embeds the chunks, and
builds a symbol graph of imports/calls. Ask "how does auth work?" and a
LangGraph agent uses hybrid retrieval to find entry points, then
traverses the symbol graph to pull in the dependent code retrieval
missed, answering with file:line citations streamed over SSE.

The thesis to be proven at the Phase 3 checkpoint: **retrieval finds
entry points; graph traversal finds the answer.** The README's headline
is a measured comparison of naive chunking vs AST chunking vs
AST + agent.

v1 scope: public GitHub repos, Python only, single user, no auth.

## Document map

| File | Role |
|---|---|
| `CLAUDE.md` | Auto-loaded every session: stack, hard rules, conventions |
| `docs/SPEC.md` | Full technical detail — schema, algorithms, tool signatures, contracts, constants (§12) |
| `docs/ROADMAP.md` | Phases 0–6, done-when criteria, status table |
| `docs/DECISIONS.md` | Append-only decision log — read before proposing changes |
| `docs/EVAL.md` | **FROZEN** benchmark: 20 questions + ground truth |
| `docs/prompts/phase-N.md` | Per-phase prompts, written just-in-time |
| `docs/samples/phase1-sample.txt` | 30-chunk sample for human spot-check |
| `docs/HANDOFF.md` | This file |

## Phase status

| Phase | State | Notes |
|---|---|---|
| 0 Foundations | ✅ done | Scaffold, compose, migrations, /health, worker |
| 1 Parse & chunk | ✅ done | tree-sitter chunking, CLI, EVAL.md frozen |
| 2 Store & retrieve | ⛔ blocked → resume next | Gate A green, Gate B WDAC-blocked |
| 3 Symbol graph & agent | not started | The hard phase; escalate model here |
| — Go/no-go checkpoint | — | Decide on real eval numbers before web work |
| 4 API & worker | not started | Needs Redis (Upstash) |
| 5 Frontend | not started | |
| 6 Evidence & ship | not started | |

## What exists in code

**Phase 0 — scaffold.** `docker-compose.yml` (pgvector/pg16 + redis,
healthchecks, named volume — unused so far, we're on Neon).
Backend on uv: FastAPI `GET /health` with a DB-tolerant lifespan,
`app/config.py` (Settings + every SPEC §12 constant by name),
`app/db/pool.py`, `app/worker.py` (ARQ, one `ping` task),
`001_init.sql` (repos table), idempotent `scripts/migrate.py`,
`tests/test_health.py`, ruff/mypy/pytest config, `.env.example`.
Frontend: Next.js 15 App Router, TS strict, Tailwind, one shadcn Button,
one page.

**Phase 1 — ingestion CLI.** `app/ingest/`:
- `clone.py` — shallow clone, always-cleanup, Windows read-only rmtree handler
- `filters.py` — SPEC §2.2 selection with skip-by-reason counts
- `tokens.py` — heuristic `len//4` counter behind a protocol
- `parser.py` — tree-sitter module / class-skeleton / function / method
  chunks; full dotted qualnames; 1-based lines; decorated, async, and
  nested defs handled; `has_error` files skipped
- `chunker.py` — enrichment header + oversize split on statement boundaries
- `cli.py` — `python -m app.ingest.cli <url> [--dump] [--sample N]`
Plus `app/exceptions.py` and `tests/ingest/` (33 tests).

**Benchmark run:** encode/httpx @ `b5addb64f0161ff6bfe94c124ef76f6a1fba5254`
→ 60 files, 1371 chunks (module 63, class 133, function 694, method 481),
102 oversize splits, 0 errors, ~5s.

**Phase 2 — dependencies only.** `sentence-transformers`, `pgvector`,
`pyyaml` and the torch stack are in `pyproject.toml`/`uv.lock`,
pre-staged for the environment move. No Phase 2 code written, no schema
beyond 001.

## Environment situation (important)

Development began on a Windows machine under a **WDAC (Application
Control) policy** that blocks unsigned native binaries. Consequences:

- **ruff cannot execute** (os error 4551). Phases 0–1 were written
  ruff-clean but unverified. **First task on the new machine:
  `uv run ruff check .` and clear the deferred-ruff notes in ROADMAP.**
- mypy ran via a pure-Python build; it passes.
- tree-sitter loaded fine — Phase 1 completed on that host.
- **torch is blocked** (`torch._C` DLL, at import, before any download).
  This kills sentence-transformers, so embeddings and the reranker
  cannot run there. No workaround was attempted by design.

**Resolution:** backend development moves to an unrestricted personal
machine. If that machine is Windows, prefer WSL2 — torch, Docker, and
the eventual Railway/Fly deploy targets are all Linux-native.

**The database is unaffected:** Postgres is on **Neon** (cloud), so the
same `DATABASE_URL` works from any machine. Compose stays in the repo
for anyone cloning on a normal machine.

## Resuming on a new machine

```bash
# install: git, uv, node + pnpm, Claude Code
git clone <repo> && cd <repo>
cp backend/.env.example backend/.env
# paste the Neon DSN into backend/.env (needs ?sslmode=require)
cd backend && uv sync
uv run python scripts/migrate.py     # should be a no-op; 001 already applied
uv run ruff check .                  # settle the deferred-ruff debt
```

`backend/.env` is gitignored and does **not** travel with the repo —
recreate it by hand. Everything else (including `uv.lock`) is in git.

Then start a fresh Claude Code session and run
`docs/prompts/phase-2.md` **from the top** — all three gates, no
resuming mid-phase.

## Immediate next steps

1. Re-run Phase 2 on the unrestricted machine. Gates A→B→C should go
   green; expect a long first run (torch install, ~2 GB reranker
   download, CPU embedding of ~1400 chunks).
2. Watch for: the real tokenizer replaces the heuristic counter, so the
   chunk count will drift from 1371 — the CLI reports the delta.
3. **The Phase 2 eval table is the first real evidence in the project.**
   Expected shape: FTS strong on locate questions, vector strong on
   conceptual, hybrid+rerank ≥ both everywhere, and flow questions
   (q16–q20) weakest across all modes — that last part is the gap
   Phase 3's graph traversal exists to close.
4. Done-when: `hybrid+rerank` hit@10 ≥ every single-signal mode. If not,
   diagnose with `scripts/debug_search.py` — never tune against
   individual EVAL questions.
5. Then Phase 3 (the hard one). Escalate the model there
   (`/model opus`, consider `/effort xhigh`) — it's the phase with real
   design ambiguity.

## Open items

- [ ] Human 30-chunk spot-check of `docs/samples/phase1-sample.txt`
      (last unticked Phase 1 done-when)
- [ ] ruff verification pass on the new machine
- [ ] Redis provisioning (Upstash free tier) — needed by Phase 4, not before

## Working agreement (why this project is set up this way)

- **Phase prompts are written just-in-time**, after the previous phase
  reports back — never batched in advance, because each phase teaches
  something that changes the next.
- **EVAL.md is frozen.** Questions and ground truth are never edited,
  and retrieval is never tuned against an individual question. Fixes
  are generic, then the full eval reruns.
- **Every architectural choice gets a DECISIONS.md entry** so it isn't
  relitigated in a later session.
- **Done means:** code + tests passing + lint/type clean + ROADMAP
  status updated + DECISIONS entry where relevant.
- CLI-first, backend-first, UI last. The go/no-go checkpoint after
  Phase 3 exists so no web code gets written on an unproven core.
