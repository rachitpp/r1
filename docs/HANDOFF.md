# HANDOFF.md — project state

**Last updated:** 2026-07-27 · **Current position:** **Phase 6 in progress.**
Backend, frontend, and docs are done, the README comparison table is filled from
measured numbers, and the clean-clone stranger re-run passed. **`demo.gif` is
the only thing left before v1 is complete, and it needs a human.**

**Outstanding:**
1. ~~The README comparison table is a placeholder.~~ **Done 2026-07-27.** The
   naive eval had in fact completed — its block was already the last one in
   EVAL.md — and a re-run reproduced it question-for-question. Table filled.
   **The result is a null one at hit@k:** naive ties AST at 0.95 hybrid hit@10
   and beats it on FTS (0.90 vs 0.80). Reported straight rather than resized;
   the AST case now rests on the symbol graph and the answer-level numbers,
   which is where it always actually lived.
2. ~~The stranger re-run has not been done.~~ **Done 2026-07-27** on a fresh
   clone at `31038ed`: every README step worked and **nothing required off-page
   knowledge**. One gap found and fixed (cold-machine model download); the cold
   path itself remains untested because every cache here was warm. Full log in
   "Immediate next steps" below. It also closed Phase 0's two never-executed
   done-when criteria — this was the project's first host with a Docker daemon.

**Deliberately not done:** a live deployment. Phase 6 finishes local-first;
`docs/DEPLOY.md` is the followable guide, written but never executed (DECISIONS
2026-07-27). Standing it up is an afternoon whenever a URL is actually wanted.

**Human to-do:** record `demo.gif` — the README carries a labelled placeholder
at the top. Submit a repo, let the progress bar move, ask a question, click a
citation. It is the first thing anyone sees.

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
| 3 Symbol graph & agent | ✅ **done** | Graph + six tools + LangGraph loop; thesis supported, narrowly |
| — Go/no-go checkpoint | ✅ **GO** | Scoped, not rounded up — see the three-tier finding below |
| 4 API & worker | ✅ **done** | §8 API + §9 SSE + ARQ ingest; Redis Cloud free tier |
| 5 Frontend | ✅ **done** | Submit/status/chat + Shiki viewer; custom `useRepoChat`, no AI SDK |
| 6 Evidence & ship | 🚧 in progress | Table filled (naive ties AST at hit@10), finding (a) corrected to MODEL-DEPENDENT, stranger re-run passed; **only `demo.gif` left** |

## Phase 3 outcome — the thesis, and exactly how strong it is

**Checkpoint: GO.** The claim is scoped, not rounded up. Full detail in
DECISIONS "Phase 3 FINAL RESULT"; the same three tiers are the README's core
claim.

- **(a) MODEL-DEPENDENT** *(downgraded from STRONG on 2026-07-27; the earlier
  "every run on both models" was read off the pre-temperature-pin runs and is
  wrong)* — **q10 — the only question no retrieval mode reaches in any
  condition — is answered by the agent in 3/3 controlled temperature-0 runs on
  Mistral and 0/3 on Vertex (0/2 distinct results: two of Vertex's three blocks
  are byte-identical and probably a double-append), or 5/5 and 2/5 across all
  runs, both Vertex hits pre-temperature-pin: graph traversal can reach what
  retrieval cannot, demonstrated on one model family and not reproduced on the
  other.** The stuffed baseline misses q10 in every run. **q14** behaves the
  same on Vertex.
- **(b) MODERATE, directionally stable** — the agent leads at symbol level in
  **6/6 runs across two model families** (Mistral +5/+4/+2, mean 0.93 vs 0.75;
  Vertex +1/+1/+2, mean 0.87 vs 0.80). **Sign stable, magnitude noisy** —
  Mistral spans 0.85–1.00 on identical configs.
- **(c) NOT SUPPORTED** — graph-tool use does *not* predict correctness. At
  temperature 0 the two models invert perfectly (Mistral: misses only *without*
  graph tools; Vertex: misses only *with* them). Selection effect: the agent
  reaches for graph tools on questions it finds hard, which differ by model. An
  earlier 7/7 reading was an artifact, caught and retracted.

**Do not quote the aggregate file-level scores as the result.** They are
0.90–0.95 for both modes and are retrieval-bound: the baseline gets a top-10
pool whose hit@10 is 0.95, so 19 of 20 questions have no discriminating power.
The **symbol-level** metric (added to `scripts/answer_eval.py`) is what findings
(a) and (b) rest on — it requires the answer to *name* the construct, which a
pool cannot supply by accident.

**Also against expectation:** the flow tier (q16–q20) ties in every paired cell
— one q17 miss in one controlled Mistral run is the only flow miss in 70 cells
(DECISIONS 2026-07-27, "Correction: the flow-tier 'every cell' claim"). A
ten-chunk pool already contains what those questions need. That is a finding
about the benchmark — harder cross-file questions are a v2 item.

**Temperature was uncontrolled until late.** Mistral ran at 0 while
Gemini/Vertex used the provider default 1.0. All four providers are now pinned
to 0 in `app/agent/model.py` (history in the docstring). Earlier results
reproduce qualitatively but were not like-for-like; **the six repeat runs are
the first controlled cross-model comparison.**

## The shipped agent configuration

```
AGENT_MODEL=mistral-medium-latest      # provider from the prefix
temperature=0                          # pinned on all four providers
AGENT_TOOL_CAP=8                       # hard cap, forced answer after
RERANK_ENABLED=false                   # ablated, still wired
```

`search_code` uses the **default** retrieval pipeline (RRF fusion, rerank off,
tests excluded). Never pass `rerank=True`.

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
uv run python scripts/migrate.py     # no-op if 001–010 are already applied
uv run pytest && uv run ruff check . && uv run mypy app
cd ../frontend && pnpm install
```

`backend/.env` is gitignored and does **not** travel with the repo —
recreate it by hand. Everything else (including `uv.lock`) is in git.

The Neon DB already holds the ingested benchmark corpus at the pinned SHA, so
a re-ingest is only needed if the schema or chunker changes. Verify with:

```sql
SELECT count(*) FILTER (WHERE NOT c.is_test) AS impl,
       count(*) FILTER (WHERE c.is_test)     AS test
  FROM chunks c
  JOIN repo_snapshots sn ON sn.id = c.snapshot_id
  JOIN repo_sources   s  ON s.id  = sn.source_id
 WHERE s.url = 'https://github.com/encode/httpx'
   AND sn.strategy = 'ast';                                       -- 825 / 697
```

The joins are not decoration. This used to be a bare `FROM chunks`, which was
right when httpx was the only corpus and silently stopped being right once it
was not: the database holds seven sources, and httpx alone has two corpora
(`ast` and the `naive` baseline) at the *same* commit. Unscoped it now answers
`1555 | 1170`, which reads as a corrupted benchmark to anyone following this
page (corrected 2026-07-31).

## Immediate next steps — the one thing left in Phase 6

Items 1 and 2 are done (logged below with their numbers). **Only item 3,
`demo.gif`, remains, and it cannot be done from inside Claude Code.**

### 1. ~~Re-run the naive eval and fill the README table~~ — DONE 2026-07-27

```bash
cd backend
uv run python scripts/eval.py --mode vector,fts,hybrid \
  --repo c7815d7b-ab57-4b30-95ee-510e014e2ba3     # encode/httpx@naive · 2m48s
```

Ran clean and **reproduced the existing EVAL.md block exactly**, mode for mode
and question for question — so the earlier "killed mid-flight" note was wrong;
that run had completed and only the README text lagged behind it. The naive
numbers: vector 0.90 / fts 0.90 / hybrid 0.95 at hit@10, hybrid MRR 0.734.

**Naive ties AST on the headline metric.** That is a finding about hit@k on this
benchmark — a 2.3×-larger window is more likely to contain a truth symbol by
accident — not a gap to close by resizing windows. The README says so in the
table note. The parameters were fixed a priori (DECISIONS 2026-07-27) and were
not touched after seeing the result.

**Use `--mode vector,fts,hybrid`, not `--mode all`.** The `hybrid+rerank` mode
is a cross-encoder on CPU and is what made the earlier runs take 20–50
minutes; it carries no claim in the comparison table and is off by default
anyway (SPEC §5.3).

**No re-ingest was needed.** Both corpora are in the database and verified
(re-confirmed 2026-07-27):

| row | url | chunks | graph |
|---|---|---|---|
| `encode/httpx` | `https://github.com/encode/httpx` | 1522 (825 impl / 697 test) | yes |
| `encode/httpx@naive` | `…/encode/httpx#naive` | 657 (327 / 330) | no, by design |

The AST benchmark row is intact at the pinned `b5addb64` — 825/697 confirmed
both before and after the baseline ingest. The naive row exists **only** for
this table; never point a demo or the agent at it.

The placeholder is gone from `README.md`. Retrieval rows came from the
`eval.py` runs, the answer rows from the paired `stuffed` vs `agent` blocks and
the three controlled repeats per model — all copied from `EVAL.md`, none from
memory.

### 2. ~~Do the clean-clone stranger re-run~~ — DONE 2026-07-27

**Executed on a fresh clone at `31038ed`, following only the README.** Result:
**it works, and nothing required off-page knowledge.** No step needed CLAUDE.md,
SPEC.md, this file, or any prior context.

Measured, in order: `docker compose up -d --wait` both healthy **13 s** ·
`uv sync` **6.9 s** *(warm cache)* · `cp .env.example .env` + one API key ·
`migrate.py` 001–004 in **2.4 s**, re-run a clean no-op · API `/health` 200 at
**29 s** · `pnpm install` **12 s** · `pnpm dev` ready **1.8 s** · submit
`pallets-eco/blinker` → `ready` in **28.6 s** (7/7 files, 76/76 chunks) ·
question → streamed answer in **~21 s** (2 tool calls: `search_code`,
`read_file`; 5 validated citations) · citation click → `src/blinker/base.py`
lines **91–115 washed exactly**, scrolled into view, line 91 =
`def connect(self, receiver: F, ...)`, matching the file the API serves
character-for-character · **zero console errors**.

Also run from the README's Tests block: **163 backend tests pass** (including
the integration tests, against the live compose Postgres and Redis), ruff and
mypy clean; frontend `build` clean (chat page 181 kB, as recorded in Phase 5),
`lint` clean, **16 vitest tests pass**.

**README claims verified:** `.env.example` `DATABASE_URL` does match
`docker-compose.yml`; the API's first start really is ~30 s; blinker really does
ingest in under a minute.

**The one gap found, and fixed.** The README said the first API start takes
~30 s "while the embedding model loads" — but on a machine with a cold
HuggingFace cache it must **download** the model (~130 MB) first. Every cache
here (uv, HF, pnpm, Docker images) was warm, so that download never happened and
the 29 s is a *warm-cache* number. The README now says so.

**What this run did NOT test:** the cold path. `uv sync` took 6.9 s against a
warm uv cache and hardlinked its 5.3 GB venv; the README's "~6 min on a cold
cache" is still unverified, as is first-run model download time. A genuinely
cold stranger run needs a machine with no `~/.cache/uv`, no `~/.cache/huggingface`,
and no pulled Docker images. **The 18m13s dry-run baseline is therefore not
comparable to this run and was not "beaten"** — different starting conditions.

**Bonus: Phase 0 is now fully closed.** This was the first host in the project's
history with a Docker daemon, so it executed the two Phase 0 done-when criteria
that had been `[~]` since the beginning — compose health and migration
idempotency against a live Postgres. Both pass; ROADMAP updated.

### 2b. Reference — the original instructions

ROADMAP's "a stranger can run it locally from README instructions alone" is
meant to be *tested*, not assumed. Clone into a temp dir, follow **only** the
rewritten README and files it links (`CLAUDE.md`, this file, `ROADMAP.md`, and
`SPEC.md` are off-page), and reach a streamed browser answer with citations.
Log anything that still needs off-page knowledge, then tear the environment
down — containers, volumes, the clone, and any `.env` you put a real key in.

The 2026-07-27 dry run took **18m13s** clone-to-first-answer and produced the
finding list the current README was written against. It is **not** a target the
re-run above beat — that run had warm caches and is not comparable. Treat 18m13s
as the cold-machine reference and the re-run as the warm-cache one.

### 3. Record `demo.gif`

Submit a repo → progress bar advances → ask a question → tool timeline streams
→ answer with citations → click a citation → viewer scrolls to the highlighted
range. ~20 seconds. Drop it in place of the placeholder at the top of
`README.md`. It is the first thing anyone sees, so it is worth a second take.

This one cannot be done from inside Claude Code — it needs a real screen
recording.

---

**Not blocking v1:** a live URL. Follow `docs/DEPLOY.md` if you want one; expect
one or two surprises, since it has never been executed. Write a Dockerfile first
if you want reproducible builds — the guide says where.

## Reference — what exists in code

1. **Frontend (Phase 5).** `frontend/src/lib/` (typed §8 client,
   hand-rolled SSE parser, citation parse/segment — vitest-covered),
   `hooks/use-repo-chat.ts` (§9 → state, sessionStorage transcript), pages
   `/`, `/repos/[id]`, `/repos/[id]/chat` (split pane: step timeline +
   streaming answer left, python-only fine-grained Shiki viewer right).
   `useRepoChat` replaced the planned AI SDK `useChat` (DECISIONS 2026-07-27).
   Phase 6 added a client-derived `composing` status — the "writing answer…"
   line that covers the quiet gap before the first text delta.
2. **Naive baseline (Phase 6).** `app/ingest/naive.py`, reachable only via
   `python -m app.ingest.cli <url> --db --strategy naive`. SPEC §2.7 states the
   scope of the hard-rule-4 carve-out; DECISIONS 2026-07-27 states why.
3. **Retry = `POST /repos`** on the same URL; a `failed` row re-queues (backend
   unchanged, behaviour pinned by `test_post_repos_failed_repo_is_re_enqueued`).
4. **Keep the eval honest.** `scripts/answer_eval.py --dev` is the tuning set;
   the frozen 20 stay for counted measurement runs only. Phase 6's comparison
   table is measured by `scripts/eval.py`, never eyeballed.

## Open items

- [ ] Human 30-chunk spot-check of `docs/samples/phase1-sample.txt`
      (last unticked Phase 1 done-when) — still open
- [x] ruff verification pass on the new machine — clean, along with mypy and
      71 tests
- [x] Redis provisioning — Redis Cloud free tier, DSN in `backend/.env`
      (`docker compose up -d redis` still works offline)

Deferred to the v2 backlog (ROADMAP), deliberately not Phase 2 loose ends:
evaluate a code-specific reranker; re-attach §5.2 injection to fusion-only
retrieval. Both need their own eval run if revisited.

## Running the stack (three processes since Phase 5)

```bash
docker compose up -d redis                 # only if not using the cloud DSN
cd backend
uv run uvicorn app.main:app --port 8000    # terminal 1 — API
uv run arq app.worker.WorkerSettings       # terminal 2 — worker
cd ../frontend && pnpm dev                 # terminal 3 — web app :3000
curl -s -X POST localhost:8000/repos -H 'content-type: application/json' \
  -d '{"url": "https://github.com/pallets/itsdangerous"}'
curl -s localhost:8000/repos/<id>          # poll: queued→cloning→parsing→linking→embedding→ready
curl -N -s -X POST localhost:8000/repos/<id>/chat \
  -H 'content-type: application/json' -d '{"question": "..."}'
```

Both processes load the embedder at startup (~18 s each, SPEC §4) — the API
because every chat request runs `search_code`, the worker because it embeds.
`/health` answers immediately regardless, and an unreachable Postgres or Redis is
a logged warning plus a 503 from the endpoint that needs it, never a boot
failure.

**What exists in code (Phase 4).** `app/ingest/pipeline.py::run_ingest(repo_id)`
is the single pipeline, called by both `app/worker.py::ingest_repo` and the
ingest CLI. `app/api/` holds routes, Pydantic schemas, `Annotated` dependencies,
the exception→HTTP map (`errors.py`), the §9 adapter (`chat_stream.py`), and the
summaries-only allowlist (`tool_events.py`). `app/ingest/urls.py` normalizes
submitted URLs so one repo cannot become three rows.

**Three extra repos are now in the database** (`pallets/itsdangerous`,
`pallets/markupsafe` from Phase 4, `pallets-eco/blinker` from Phase 5 — note
blinker lives under the `pallets-eco` org; `pallets/blinker` 404s). The httpx benchmark row at
the pinned SHA was deliberately not touched; delete the two extras if a clean
list matters for a demo.

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
