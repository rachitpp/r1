# Phase 6 prompt — Evidence & ship (local-first finish)

> **How to use:** save as `docs/prompts/phase-6.md`, start a fresh
> Claude Code session at the repo root (Sonnet is sufficient), and
> instruct: "Read docs/prompts/phase-6.md completely, confirm Phase 5
> is done in ROADMAP.md, give me a ≤12-line plan, then proceed."
> Backend + worker + `pnpm dev` should be runnable for the final
> verification. This is the LAST phase.

---

You are starting **Phase 6 — Evidence & ship**. Goal: make the project
legible and trustworthy to a stranger in sixty seconds, and runnable by
one from the README alone. **Finish line is local-first (option B):**
the clone-to-first-answer experience is made frictionless and the
comparison table is built with real numbers; the live cloud deploy is
*documented as an executable guide, not stood up*. **Feature freeze** —
no new product capability; anything tempting goes to the v2 backlog.

## Step 0 — Orient

Read, in order:
1. `CLAUDE.md`
2. `docs/ROADMAP.md` — Phase 6 section + the three done-when criteria
3. `docs/DECISIONS.md` — the Phase 3 FINAL RESULT (three-tier), the
   Phase 2 outcome, the shipped config
4. `docs/HANDOFF.md` — full current state
5. `docs/EVAL.md` — the frozen questions and all result blocks

Confirm Phase 5 is `done`. Plan in ≤12 lines, then proceed.

## Session rules

- **Feature freeze.** The only code changes permitted this phase are:
  (1) the naive-chunking baseline harness below, (2) the onboarding
  fixes explicitly listed, (3) the two benign startup-noise fixes
  (P2), (4) committing the already-made CORS/config working-tree
  changes. Nothing else. No agent/retrieval/schema changes.
- The frozen benchmark is measured, never tuned. Naive-vs-AST numbers
  come from `eval.py`/`answer_eval.py`, not by hand.
- ruff + mypy + pytest green per commit; `pnpm build` + `pnpm lint` +
  vitest green.
- Small logical commits. This phase ends with a tagged release commit.

---

## Part A — The comparison table (the headline)

The README's hero is a measured **naive chunking vs AST chunking vs
AST + agent** comparison on the frozen 20. AST+agent numbers already
exist (Phase 2 retrieval + Phase 3 answer-level). The **naive column
does not exist yet** — build it.

1. **Naive chunking strategy.** Add a `--strategy naive` flag to the
   chunker: fixed-size character-window splits (≈1000 chars, ≈100
   overlap — record exact values in DECISIONS) with the SAME enrichment
   header format, so only the boundary logic differs from AST. No
   tree-sitter, no symbol awareness.
2. **Ingest a naive corpus** of the benchmark repo into a **separate
   repo row** (e.g. name-suffixed `httpx@naive`) via delete-and-replace
   — the AST benchmark row at the pinned SHA is NOT touched. Report
   chunk counts (naive vs AST: expect a different count).
3. **Run retrieval eval** (`eval.py --mode all`, implementation-only)
   against the naive corpus. Its symbol graph is absent/degraded, so
   the agent answer-level run on naive is retrieval-stuffing only —
   note that honestly rather than forcing an agent number on a corpus
   with no graph.
4. **Assemble the table** with THREE comparable cells at minimum:
   naive retrieval (hit@10), AST retrieval (hit@10 = 0.95), AST+agent
   (answer-level, the Phase 3 numbers). Pull AST/agent figures from the
   FRESH eval block the human is pasting below — do not use stale
   remembered numbers.

### Fresh retrieval numbers — use THESE, not remembered ones

Run on 2026-07-27, `eval.py --mode all --both-conditions`. Also appended
to `docs/EVAL.md` as the newest dated block.

**Repo:** https://github.com/encode/httpx @ `b5addb64f016` — 1522 chunks
(825 implementation, 697 test), 20 questions.

**Corpus condition:** implementation-only (default, `is_test` excluded)

| Mode | hit@3 | hit@5 | hit@10 | MRR |
|---|---|---|---|---|
| vector | 0.75 (15/20) | 0.85 (17/20) | 0.90 (18/20) | 0.722 |
| fts | 0.60 (12/20) | 0.70 (14/20) | 0.80 (16/20) | 0.503 |
| **hybrid** (default) | **0.80** (16/20) | **0.85** (17/20) | **0.95** (19/20) | **0.752** |
| hybrid+rerank | 0.80 (16/20) | 0.80 (16/20) | 0.85 (17/20) | 0.722 |

**Corpus condition:** shadowed (`--include-tests`, `is_test` included)

| Mode | hit@3 | hit@5 | hit@10 | MRR |
|---|---|---|---|---|
| vector | 0.65 (13/20) | 0.80 (16/20) | 0.85 (17/20) | 0.632 |
| fts | 0.25 (5/20) | 0.45 (9/20) | 0.60 (12/20) | 0.267 |
| hybrid | 0.70 (14/20) | 0.80 (16/20) | 0.80 (16/20) | 0.617 |
| hybrid+rerank | 0.70 (14/20) | 0.75 (15/20) | 0.75 (15/20) | 0.604 |

> **Provenance note (put a version of this under the README table):**
> freshly re-verified 2026-07-27; the Phase 2 gate is intact
> (hybrid 0.95 ≥ vector 0.90 ≥ fts 0.80). `fts` MRR improved slightly
> against the Phase 2 block (0.465 → 0.503, the FTS-fix leg) with
> hit@10 unchanged — recorded as an improvement, not a regression.

**Per-question hit@10 — the STRONG-tier scope, and it is narrow.**

- **q10 is missed by all four modes in BOTH conditions.** It is the only
  question that is. This — and only this — is the "unreachable by every
  retrieval mode in every condition" core.
- **q14 is reached by `hybrid` in the implementation-only condition**
  (re-verified today), and missed by `hybrid` in the shadowed condition.
  It is a **supporting** example, not part of the unreachable core.

Getting this distinction right is required, not optional: the current
README phrasing overclaims for q14, and a reviewer who reruns the eval
would catch it. See Part B item 3(a).

5. If the naive column is *not* dramatically worse than AST somewhere,
   that itself is a finding — report it straight; do not massage the
   corpus to manufacture a gap.

## Part B — The honest README

Rewrite `README.md` as the front door. Structure:

1. **One-line what + a 20-second demo GIF** (record the browser flow:
   submit → progress → ask → stream → citation click → highlighted
   code). If GIF tooling isn't available in-environment, insert a
   clearly-labeled placeholder and add "record GIF" to the final
   summary as the one human to-do.
2. **The comparison table** from Part A — the hero.
3. **The result, stated in three tiers exactly as DECISIONS records
   them** — do NOT round up:
   - > ⚠️ **SUPERSEDED 2026-07-27** — this instruction is preserved as the
     > prompt that was actually given. The claim it asks for is **wrong**: see
     > DECISIONS "Correction: finding (a) is model-dependent, not 'every run,
     > both models'". Finding (a) is MODEL-DEPENDENT — q10 is answered in 3/3
     > controlled runs on Mistral and 0/3 on Vertex. Do not re-derive README
     > wording from this prompt.
   - (a) STRONG: the agent answers **q10** — the one question missed by
     every retrieval mode in *both* corpus conditions (re-verified
     2026-07-27) — in every run on both models, where the stuffed
     baseline misses it every time. **Scope this to q10 alone.**
     **q14 is a supporting example, not part of the unreachable core:**
     phrase it as "the agent also answers q14, which retrieval reaches
     only inconsistently" — hybrid *does* reach q14 in the
     implementation-only condition and misses it in the shadowed one.
     Do not write "unreachable by every retrieval mode" about q14; that
     overclaims and the eval block above disproves it.
   - (b) MODERATE / directionally stable: agent leads at symbol level
     in 6/6 runs on two model families; sign stable, magnitude noisy.
   - (c) NOT SUPPORTED: graph-tool-use does not predict correctness
     (the models invert at temp 0); the earlier 7/7 was a selection
     effect, caught and retracted.
4. **"What the headline numbers do not show"** — the file-level metric
   is retrieval-bound (top-10 pool at hit@10 0.95, so 19/20 questions
   lack discriminating power); the symbol-level metric is what (a)/(b)
   rest on; the flow tier ties because a 10-chunk pool already contains
   what those questions need. This honesty section is a feature — it is
   what distinguishes this from a benchmark-gamed project.
5. **Architecture** — a short diagram (ingest pipeline → Postgres/pgvector
   + symbol graph; agent loop with the six tools; SSE to the frontend)
   and 2-3 sentences on the thesis.
6. **Methodology note** — frozen benchmark authored blind before
   retrieval existed; anti-tuning discipline; the temperature-control
   correction (the six repeat runs are the first like-for-like
   cross-model comparison).
7. **Run it locally** — see Part C; the README's run section IS the
   stranger's path.
8. **Deploy it** — see Part D.
9. Link `docs/DECISIONS.md` for the full log; keep the README skimmable.

## Part C — Onboarding fixes (from the stranger dry run, re-triaged)

B1 is already resolved (code pushed). Apply the rest. Re-verify each
against the CURRENT tree before writing — some were stale reads of the
old remote (F7's frontend half is already fixed; only its
`FRONTEND_ORIGIN` half remains).

**Was-BLOCKER (now doc/config, critical):**
- **Web path in README (B2):** add a "Run the web app" section — three
  processes, all required: `uvicorn app.main:app`, `arq
  app.worker.WorkerSettings`, `pnpm dev` (in `frontend/`). State that
  the worker is mandatory or ingestion never starts (this subsumes F5).
- **Default model + key source (B3):** change `.env.example` default to
  the project's actual measured primary — `AGENT_MODEL=mistral-medium-latest`
  — and add `# Get a key at console.mistral.ai` beside
  `MISTRAL_API_KEY`. **Verify `gemini-3.5-flash` is not left as a
  default anywhere** (it was never measured and may not be a live id).
  Name the required key in the README run step.

**FRICTION → fix:**
- **F1 Prerequisites block:** Python 3.11+, Node version, pnpm, the uv
  install one-liner. Add `packageManager`/engines to `frontend/package.json`.
- **F2 DATABASE_URL comment:** it already matches compose — change the
  `.env.example` comment to say so; stop implying edits.
- **F3 duration warnings:** note `uv sync` (~6 min cold) and ingest
  (~8 min, emits HF logs then goes quiet) next to those commands.
- **F4 warm-up note:** "first API start takes ~30s while the embedding
  model loads; the port refuses connections until it's ready."
- **F6 provider list in `.env.example`:** add the `mistral*` row to the
  `AGENT_MODEL` prefix comment (it's the primary provider yet was
  omitted).
- **F7 remaining half:** document `FRONTEND_ORIGIN` in the backend
  `.env.example` (the frontend `.env.local.example` half is already
  done).

**POLISH → fix the cheap ones:**
- **P1 friendly CLI errors:** catch `AgentError`/config errors in the
  CLI entrypoints and print one line, not a traceback.
- **P2 startup noise:** quiet the HF request logger to WARNING; fix the
  deprecated `get_sentence_embedding_dimension` call
  (`app/ingest/embedder.py:44`). Leave the benign "Token indices …
  683 > 512" alone but confirm it's harmless.
- **P3 sample output in README:** paste a trimmed real answer with its
  citation block so a newcomer can confirm success.
- **P4 migrate race:** change README to `docker compose up -d --wait`.

**Commit the pending CORS/config changes:** the uncommitted working-tree
edits (multi-origin CORS in `app/main.py`, `app/config.py`,
`backend/.env.example`) ship as part of this phase — they're needed for
Part D's deploy config. Ensure `FRONTEND_ORIGIN` handling supports both
local and a deployed origin.

## Part D — Deploy guide (documented, not executed)

Write `docs/DEPLOY.md` — a real, followable guide for the three-process
topology, without standing it up:
- **Frontend → Vercel:** root `frontend/`, env `NEXT_PUBLIC_API_URL` =
  the deployed API URL.
- **API + worker → Railway or Fly:** TWO processes from the same image
  (web: uvicorn; worker: arq) — call out that the worker is a
  persistent process, NOT serverless, and needs its own service. Env:
  `DATABASE_URL`, `REDIS_URL`, `MISTRAL_API_KEY`, `AGENT_MODEL`,
  `FRONTEND_ORIGIN` = the Vercel URL (this is why CORS had to become
  multi-origin).
- **Data:** Neon (already used) + Redis Cloud/Upstash (already used) —
  no change, they're already cloud.
- **Gotchas to state:** the ARQ poll-delay/command-budget note (already
  in DECISIONS); the embedder cold-start on the API service; running
  migrations against the prod DB once on first deploy.
- A short "why this isn't one-click" note: the worker is the reason.

Add a one-liner to the README pointing at `docs/DEPLOY.md` and stating
the project runs locally today, with cloud deploy documented there.

## Verification

1. `cd backend && uv run pytest && uv run ruff check . && uv run mypy app`
2. `cd frontend && pnpm build && pnpm lint && pnpm vitest run`
3. **The stranger re-run (the real done-when):** in a **clean clone**
   into a temp dir, follow ONLY the rewritten README — reach a first
   streamed browser answer. Log any step that still needs off-page
   knowledge as a residual finding. Tear down the temp clone and any
   temp env after. (This is the "a stranger can run it from the README
   alone" criterion, actually tested.)
4. Confirm the AST benchmark row at the pinned SHA is intact and the
   naive corpus is a separate row (or cleaned up) — the demo DB
   shouldn't be cluttered.

## Wrap up

1. ROADMAP Phase 6 → done; tick the three done-when boxes (deploy
   criterion satisfied by `docs/DEPLOY.md` + a working local run under
   option B — note explicitly that a live URL is deferred by choice,
   not blocked).
2. DECISIONS: naive-baseline parameters + the naive-vs-AST result;
   the option-B finish (local-first, deploy documented); the residual
   findings from the stranger re-run, if any.
3. HANDOFF: mark the project **v1 complete**; list what a future
   session would do to stand up the live deploy (Part D executed) and
   the v2 backlog.
4. **Tag the release:** final commit, then annotated tag `v1.0`.
5. ≤12-line summary: the comparison table, the honest one-paragraph
   result, the stranger-run outcome, and the one human to-do (record
   the demo GIF, if it couldn't be generated in-environment).