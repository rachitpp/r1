# HANDOFF.md — project state

**Last updated:** 2026-07-26 · **Current position:** **Phase 2 done** on an
unrestricted host. Next up: Phase 3 (symbol graph & agent) — the hard one.

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
| 2 Store & retrieve | ✅ **done** | Gate PASS: **hybrid 0.95 ≥ vector 0.90 ≥ fts 0.80** |
| 3 Symbol graph & agent | **next** | The hard phase; escalate model here |
| — Go/no-go checkpoint | — | Decide on real eval numbers before web work |
| 4 API & worker | not started | Needs Redis (Upstash) |
| 5 Frontend | not started | |
| 6 Evidence & ship | not started | |

## Phase 2 outcome — read this before touching retrieval

**Result.** Default pipeline `hybrid` = **hit@10 0.95 (19/20)**, hit@5 0.90,
hit@3 0.80, MRR 0.755 — dominating `vector` (0.90) and `fts` (0.80) at **every**
k and at MRR. The done-when was amended (logged in ROADMAP, not silent) to name
the *default pipeline* rather than the `hybrid+rerank` configuration; the intent
— "the full pipeline must not do worse than its simplest part" — is unchanged
and the bar was not lowered.

Three things a newcomer will otherwise get wrong:

1. **Retrieval targets implementation by default.** Test chunks are flagged
   `is_test` at ingest (SPEC §2.6) and filtered from both fusion CTEs (§5.4).
   On httpx that is **697 of 1522 chunks (46 %)**. Tests were systematically
   outranking implementation for NL questions — in the lexical leg *and* the
   cross-encoder — because tests are written in user vocabulary while
   implementation is terse. `--include-tests` reproduces the old shadowed
   condition; both are in EVAL.md. **Caveat on record:** all 20 truth files are
   implementation, so exclusion raises scores by construction; the
   justification is product intent and mechanism generality, and the
   counterfactual stays measurable.
2. **The reranker is ablated — off by default, but not deleted.**
   `RERANK_ENABLED=false`. It measured worse-or-equal to plain fusion at every k
   and at MRR in *both* corpus conditions. It inverts correct orderings by wide
   margins (q09: fusion #4 → CE #38; q14: #5 → #20), preferring chunks whose
   surface vocabulary echoes the question. Still wired and lazily loaded, and
   `eval.py --mode hybrid+rerank` still works, so the ablation is permanently
   measurable. **Do not re-enable it without an eval.**
3. **§5.2 symbol injection is dormant, by design not by neglect.** Injected
   chunks carry no RRF score, so fusion-only mode cannot order them. Accepted:
   the benchmark is deliberately user-vocabulary (11/20 questions have zero
   lexical overlap with answer identifiers) so it cannot measure injection
   anyway, and in Phase 3 exact-identifier lookup becomes
   `get_definition`/`find_references` against the `symbols` table — direct hits,
   no ranking to lose. `search_code` stays semantic entry-point finding.

**The one remaining miss is q10** ("how multipart bodies are built"), missed by
every mode in every condition — its answer chunk never enters the pool.
Retrieval alone cannot reach it. **That is Phase 3's named target** and the
concrete proof case for the project's thesis: *retrieval finds entry points;
graph traversal finds the answer.* q09 and q15 were previously assumed to be in
the same category and turned out to be merely test-shadowed — they now hit.

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

**Phase 2 — store, retrieve, evaluate.**
- `002_files_chunks.sql` (files + chunks, HNSW + GIN), `003_is_test.sql`
  (test flag). **Phase 3's symbols/edges migration is `004`.**
- `app/ingest/embedder.py` — the only module importing sentence-transformers;
  lazy factories so importing retrieval does not drag in torch.
- `app/ingest/filters.py::is_test_path()` — corpus-wide path rule (§2.6).
- `app/db/queries.py` — batched write path; `cli.py --db` ingests to Postgres.
- `app/retrieval/hybrid.py` — RRF fusion in one SQL statement, `include_tests`
  and `rerank` switches, `hybrid_search()` as the single public entry point.
- `scripts/eval.py` — four modes × two corpus conditions, hit@3/@5/@10 + MRR,
  appends one labelled block to EVAL.md. `scripts/debug_search.py` — per-signal
  ranks side by side; reach for this first when a question misses.
- Tests: **71 green** (70 unit + 1 integration). ruff + mypy clean.

**Benchmark corpus:** encode/httpx @ `b5addb64f0161ff6bfe94c124ef76f6a1fba5254`
→ 60 files, **1522 chunks** (825 implementation / 697 test) with the real
tokenizer (1371 under Phase 1's heuristic counter).

## Environment situation — RESOLVED

Development began on a Windows machine under a **WDAC (Application Control)
policy** that blocked unsigned native binaries: `import torch` raised
`ImportError: DLL load failed … Application Control policy has blocked this
file`, and `ruff` could not execute (os error 4551). A later 8 GB host loaded
torch but **swap-thrashed to a standstill** on the 2.4 GB reranker.

**Both are resolved.** Backend now runs on an unrestricted Linux host (15 GB
RAM, 4 CPUs, no GPU). torch imports, both models load, `ruff` runs clean, and
the integration test — never once executed on the old hosts — passes.

**Performance reference for this class of box** (4 CPUs, no GPU), so future
estimates are not wishful: full ingest ≈ **7 min** (embed 1522 chunks at ~4/s);
`eval.py --mode all` ≈ **25 min**; `--both-conditions` ≈ **75 min**. The eval
prints only at the end — there is no progress output to watch.

**The database is unaffected by host moves:** Postgres is on **Neon** (cloud),
so the same `DATABASE_URL` works anywhere. Compose stays in the repo and works
fine for a local pg + redis.

## Resuming on a new machine

```bash
# install: git, uv, node + pnpm, Claude Code
git clone <repo> && cd <repo>
cp backend/.env.example backend/.env
# paste the Neon DSN into backend/.env (needs ?sslmode=require)
cd backend && uv sync
uv run python scripts/migrate.py     # no-op if 001–003 are already applied
uv run pytest && uv run ruff check . && uv run mypy app
cd ../frontend && pnpm install
```

`backend/.env` is gitignored and does **not** travel with the repo —
recreate it by hand. Everything else (including `uv.lock`) is in git.

The Neon DB already holds the ingested benchmark corpus at the pinned SHA, so
a re-ingest is only needed if the schema or chunker changes. Verify with:

```sql
SELECT count(*) FILTER (WHERE NOT is_test) AS impl,
       count(*) FILTER (WHERE is_test)     AS test FROM chunks;   -- 825 / 697
```

## Immediate next steps

1. **Phase 3 — symbol graph & agent.** The hard one, and the phase with real
   design ambiguity: escalate the model (`/model opus`, consider
   `/effort xhigh`). Write `docs/prompts/phase-3-prompt.md` just-in-time first,
   per the working agreement below.
2. `004_symbols.sql` — symbols + edges (SPEC §6). Note the number: `003` is
   already taken by `is_test`.
3. **`search_code` must consume the DEFAULT pipeline** — `hybrid_search()` with
   rerank off and tests excluded. Do not pass `rerank=True`; that configuration
   measured worse at every k (SPEC §7.1).
4. **q10 is the proof case.** It is the sole EVAL miss and no retrieval mode in
   any condition reaches it. If Phase 3's graph traversal answers q10, the
   project's thesis is demonstrated on the record rather than asserted.
5. Redis (Upstash free tier) is not needed until Phase 4; local compose covers
   it meanwhile.

## Open items

- [ ] Human 30-chunk spot-check of `docs/samples/phase1-sample.txt`
      (last unticked Phase 1 done-when) — still open
- [x] ruff verification pass on the new machine — clean, along with mypy and
      71 tests
- [ ] Redis provisioning (Upstash free tier) — needed by Phase 4, not before

Deferred to the v2 backlog (ROADMAP), deliberately not Phase 2 loose ends:
evaluate a code-specific reranker; re-attach §5.2 injection to fusion-only
retrieval. Both need their own eval run if revisited.

## Phase 3 gotchas worth knowing before you run anything

**Provider model ids are not portable.** `vertex:gemini-3.5-flash` does not
exist — Vertex's catalogue differs from AI Studio's. This project's GCP
account has `gemini-2.5-flash` and `gemini-2.5-pro`; the M3 confirmation run
used **`vertex:gemini-2.5-flash`**, which is what the EVAL.md block records.
`gemini-2.5-flash` is reachable on Vertex despite AI Studio reporting it "no
longer available to new users". Verify a model id against the provider you are
actually calling.

**Vertex credentials must reach `os.environ`.** `google-auth` reads
`GOOGLE_APPLICATION_CREDENTIALS` from the process environment; pydantic-settings
loads `.env` into `Settings` and never exports it. `app/agent/model.py` bridges
the two. A hand-rolled script that sets the variable itself will work while the
application path fails — that is how this hid.

**Free-tier shapes differ in the dimension that matters.** AI Studio is
**20 requests/day/model** — about two agent runs — so it is smoke-test only.
Mistral is token-metered (1 RPS / 500K TPM / ~1B per month), which is what an
agent loop needs. For agent workloads evaluate a tier by tokens/day, not
requests/day. See DECISIONS "Provider roles".

**Mistral is not deterministic at `temperature=0`.** The identical dev-set
configuration scored 7/7 and then 5/7. Budget roughly ±2 questions of noise on
a 7-question set; single runs cannot resolve small deltas.

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
