# Phase 2 — finish on a ≥16 GB host (handoff prompt)

> **How to use:** open a fresh Claude Code session at the repo root on a machine
> with **≥16 GB RAM** (a GPU is a bonus). Paste/point it at this file and say:
> "Read this completely, then do PART 2." It is self-contained; PART 1 is the
> context, PART 2 is the exact work to do.

---

## PART 1 — THE PROBLEM

**Project.** A codebase-onboarding RAG app (see `CLAUDE.md`). Phase 2 is the
retrieval layer: ingest a Python repo → chunk on AST boundaries → embed
(`bge-small`, pgvector) + Postgres FTS → `retrieval.hybrid_search()` fuses
vector ⊕ FTS with RRF, adds exact-symbol injection, and reranks with a
cross-encoder (`bge-reranker-v2-m3`). Benchmark: `encode/httpx` pinned at
`b5addb64…`, 20 frozen questions in `docs/EVAL.md`, metric `hit@k`.

**What is built, committed, and verified (model-free parts):**
- Migrations `001`+`002`, embedder module, DB write path, ingest CLI `--db`.
- `retrieval/hybrid.py`: RRF fusion, §5.2 injection, rerank; `search(mode=…)`.
- `scripts/eval.py` (modes: `vector|fts|hybrid|hybrid+rerank`, or a comma list;
  reports **hit@3 / hit@5 / hit@10 / MRR**) and `scripts/debug_search.py`.
- Unit tests pass (`13 passed`): RRF math, identifier regex, qualname match,
  EVAL yaml parse, first-hit-rank/MRR math. `ruff` clean.
- `encode/httpx` ingested to Postgres (Neon) at the pinned SHA: **1522 chunks**.
- **FTS fix applied and measured:** the FTS leg used `plainto_tsquery` (ANDs all
  terms) and returned **0 rows** for NL questions, so hybrid collapsed to
  vector-only. Now it OR-combines the stopword-stripped lexemes (SPEC §5.1
  updated). **fts hit@10 went 0.05 → 0.65 (13/20)** — measured, because `fts`
  mode is model-free.

**What is BLOCKED and why (this is the whole reason for the handoff).**
The dev machine was **8 GB RAM**. `bge-reranker-v2-m3` is ~2.4 GB and even
`bge-small` pulls in torch; loading either **swap-thrashes to a stall** (process
sleeps at ~0 % CPU, torch never resident). So every model-loading step could
**not be run**:
- `vector`, `hybrid`, and `hybrid+rerank` eval modes.
- The marked integration test (`tests/retrieval/test_integration_db.py`).
- The Phase 2 **done-when gate**.

**The open questions to resolve (both need a model load):**
1. **Did the FTS fix make `hybrid` beat `vector`?** Unknown. `vector` hit@10 =
   **0.85 (17/20)**. `fts` is now 0.65 but its hits are a subset of vector's, and
   the three questions vector misses (q09/q10/q15) are semantically hard — FTS
   surfaces their truth files only at mid-ranks (q09 #32, q15 #14, q10 #176). RRF
   *might* still lift q09/q15 into the fused top-10 if their truth also sits in
   vector's top-40. Only the eval can say.
2. **Does `hybrid+rerank` clear the done-when** ("hit@10 ≥ every single-signal
   mode")? The **first** eval (before the FTS fix) FAILED: hybrid+rerank@10 =
   **0.80 (16/20) < vector 0.85** — the cross-encoder evicted one truth chunk
   (q14) from the top-10. That must be re-measured now that FTS is fixed, and at
   hit@3/MRR (a reranker optimizes low-k, which hit@10 barely reflects).

**Read these for full context:** `docs/DECISIONS.md` (all `2026-07-25` entries —
rerank regression, FTS dead/fixed, MRR addition), `docs/EVAL.md` (frozen
questions + appended result blocks), `docs/phase-2-rerank-review.md` (the
detailed failure analysis + options A–E).

**Hard constraints (carry over):** never tune retrieval against individual EVAL
questions; `EVAL.md` is frozen (append result blocks, never edit them); all
retrieval goes through `hybrid_search()`; the FTS OR-construction is the accepted
§5.1 form; keep the reranker wired.

---

## PART 2 — THE SOLUTION (do this)

**0. Orient.** Read `CLAUDE.md`, `docs/ROADMAP.md` (Phase 2), the `2026-07-25`
`DECISIONS.md` entries, `docs/EVAL.md`, `docs/phase-2-rerank-review.md`.

**1. Environment.**
```bash
cd backend
uv sync
# backend/.env needs DATABASE_URL (Postgres) and optionally HF_TOKEN.
# If reusing the same Neon DATABASE_URL, the data is already ingested (skip step 2).
# Otherwise start a DB: `docker compose up -d` from repo root, or a fresh Neon.
```

**2. Migrate + ingest (idempotent; skip if reusing the populated Neon DB).**
```bash
uv run python scripts/migrate.py                                   # applies 001+002
uv run python -m app.ingest.cli https://github.com/encode/httpx --db
```
Expect ~1522 chunks, head `b5addb64…` (must equal the EVAL pinned SHA), status
`ready`. Ingest is delete-and-replace, safe to re-run.

**3. Run the full eval — the step the 8 GB box could not do.**
```bash
uv run python scripts/eval.py --mode all
```
~10–15 min (loads bge-small + the 2.4 GB reranker). It prints and appends a
dated block to `docs/EVAL.md` with hit@3/5/10 + MRR for all four modes.

**4. Read the acceptance signals off that table.**
- **Sanity:** `fts` hit@10 ≈ 0.65 (confirms the FTS fix is live here).
- **Q1 — did FTS help fusion:** is `hybrid` hit@10 **>** `vector` hit@10 (0.85)?
- **Q2 — the done-when gate:** is `hybrid+rerank` hit@10 **≥** every single-signal
  mode (`vector`, `fts`)? Also compare hit@3 and MRR across modes.

**5. Decide (do NOT force a pass).**
- **If the done-when PASSES** (`hybrid+rerank` hit@10 ≥ max(vector, fts)):
  Phase 2 is done. Tick the `docs/ROADMAP.md` Phase 2 checkboxes with the real
  numbers, set status → `done`, add a `DECISIONS.md` entry recording the table,
  run step 6, commit.
- **If it FAILS:** diagnose, don't wrap up. Run
  `uv run python scripts/debug_search.py --repo https://github.com/encode/httpx --query "<worst question>"`
  to see the per-signal ranks. The pre-diagnosed fix is the **fusion floor /
  score blend** (Option A in `phase-2-rerank-review.md`; rerank entry in
  DECISIONS): make the reranked top-k never rank a strong-fusion hit out — e.g.
  `final = α·norm(cross_encoder) + (1−α)·norm(rrf)`, α chosen on a validation
  signal, not fitted to a question. It is a **SPEC §5.3 change**, so update SPEC
  §5.3 + add a DECISIONS entry, then rerun `--mode all` and report what changed.
  If the reranker only helps at hit@3/MRR (not hit@10), record that and let the
  team decide whether hit@10 is the right gate (see review doc §7).

**6. Tests + quality gates.**
```bash
uv run pytest                       # unit + integration (integration needs DB + models)
uv run ruff check . && uv run mypy app
```

**7. Wrap up.** Update `docs/ROADMAP.md` Phase 2 status + checkboxes; add a
`DECISIONS.md` entry with the final eval table and any §5.3 change; commit in
small logical commits. Then Phase 2's go/no-go is real and Phase 3 can start.

**Reminders:** no per-question tuning; `EVAL.md` frozen; the FTS OR-fix stays;
report the numbers honestly whichever way they fall.
