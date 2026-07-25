# DECISIONS.md — append-only decision log

New entries go at the bottom. Never edit or delete an old entry;
supersede it with a new one that references it.

## 2026-07-24 — pgvector over a dedicated vector DB
Postgres is already required for the symbol graph, FTS, and job state.
pgvector keeps hybrid RRF fusion in one SQL query and removes an entire
service. Rejected: Qdrant, Chroma.

## 2026-07-24 — ARQ over Celery
The codebase is async end-to-end; ARQ is Redis-native and async-first
with minimal configuration. Rejected: Celery (sync-first, heavy config),
Dramatiq, Postgres-as-queue.

## 2026-07-24 — SSE over WebSockets
Streaming is one-directional server→client. SSE survives proxies and
simplifies both ends. Chat is POST with a streamed text/event-stream
response consumed via fetch/ReadableStream — not GET/EventSource.

## 2026-07-24 — Plain numbered SQL migrations over Alembic
Project size doesn't justify Alembic. `NNN_*.sql` applied in order by
`scripts/migrate.py` against a `schema_migrations` table. Applied
migrations are never edited — only appended.

## 2026-07-24 — Repo file contents stored in Postgres; clone is ephemeral
After ingestion, file text lives in the `files` table; `read_file`,
`list_directory`, and the frontend viewer all serve from the DB. No
persistent disk is needed for serving. The clone dir is deleted in a
`finally` block.

## 2026-07-24 — File selection starts from `git ls-files`
Tracked files inherit .gitignore semantics for free; we never
reimplement gitignore matching. Our own filters (SPEC §2.2) apply on
top of the tracked set.

## 2026-07-24 — "Called by" attached at context-assembly time, not embedded
Chunk headers embed only file/symbol/signature/file-level imports.
Caller information is appended when chunks enter the agent context
(SPEC §7.4). Keeps embeddings stable as the graph changes and avoids
re-embedding passes.

## 2026-07-24 — Shallow clone (depth 1)
Commit-history indexing is deferred to v2, so v1 clones with
`--depth 1 --single-branch`.

## 2026-07-24 — Symbol uses the full dotted qualname everywhere
SPEC §2.4's example header previously showed a class-relative symbol
(`AuthMiddleware.verify_token`), which contradicted §3's definition of
`qualname` as the full dotted path (`pkg.module.Class.method`).
Reconciled in favor of the **full dotted qualname everywhere**: both
chunk `symbol` fields and header `Symbol:` lines carry it. Module-path
rule: `a/b/c.py` → `a.b.c`, `a/b/__init__.py` → `a.b`; a module chunk's
symbol is that module path, a method's is `<module>.<Class>.<method>`.
Edited the §2.4 example to match.

## 2026-07-24 — Heuristic token counter in Phase 1 (len//4)
The real embedding tokenizer ships with sentence-transformers in Phase 2
(native deps we are not installing in Phase 1). Phase 1's oversize-split
logic (SPEC §2.5) needs a `token_len`, so `app/ingest/tokens.py` provides
a `TokenCounter` protocol with a `HeuristicTokenCounter` (`len(text)//4`).
Phase 2 swaps in the model tokenizer via the same protocol and re-checks
oversize splits against `CHUNK_TOKEN_MAX`. Chunk boundaries (AST nodes)
are unaffected — only the oversize threshold is approximate until then.

## 2026-07-24 — Phase 1 benchmark repo: encode/httpx
Picked `encode/httpx` as the pinned benchmark: well-known, mid-size, pure
Python, clean package layout, exercises decorators/async/classes. Fallback
`pallets/flask` if httpx fails structurally. SHA recorded in docs/EVAL.md
when the benchmark run is approved.

## 2026-07-24 — Phase 2 blocked on this host by WDAC; backend moves machines
**What failed.** Phase 2's Gate B: `import torch` (the backend for
`sentence-transformers`, used for both embeddings and the reranker) raised
`ImportError: DLL load failed while importing _C: An Application Control
policy has blocked this file.` The block happens at import of torch's
compiled `_C` extension, *before* any model download — so it is a Windows
Application Control (WDAC) policy block, not a network/HuggingFace issue.
Gate A (Postgres via Neon) passed; Gate C (reranker, ~2 GB) was not run
because it rides on the same torch backend and would fail identically.

**Why no workaround.** The Phase 2 prompt's Gate B rule is explicit: a
DLL/policy error means stop, not improvise. CPU-only wheel swaps, vendored
builds, code-signing, or substituting a non-torch embedder would all either
violate the locked stack (CLAUDE.md) or the frozen retrieval design, so none
was attempted.

**Contrast with Phase 1.** tree-sitter's native extension passed the same
class of gate and loaded fine under WDAC; torch did not. WDAC blocks specific
unsigned native binaries (ruff and mypyc in Phase 0, torch here) while
allowing others (tree-sitter) — so "native" alone doesn't predict the block.

**Resolution.** Backend development moves to an unrestricted machine (WSL2 or
another host) where torch's native libs are permitted. The database is
unaffected: Neon is cloud-hosted, so the same `DATABASE_URL` works unchanged
from the new environment. Phase 2 deps are pre-staged in pyproject/uv.lock so
the move is a `uv sync` away. ROADMAP Phase 2 stays "not started".

## 2026-07-25 — WDAC block resolved on the new machine; ruff now verified
Supersedes the operational status (not the analysis) of the 2026-07-24
WDAC entry. Backend development moved to an unrestricted macOS host. All
three Phase 2 environment gates pass here: Postgres reachable (Neon,
unchanged `DATABASE_URL`), `import torch` (2.13.0) loads, `bge-small`
embeds (384-d), and the `bge-reranker-v2-m3` CrossEncoder predicts — the
exact `torch._C` DLL/policy block from 2026-07-24 is gone. The native
binaries WDAC previously blocked now run: `ruff check .` executes and is
clean (three trivial Phase 0/1 findings — UP035, I001, B905 — fixed in
one commit), and `mypy app` runs on the normal compiled (`mypyc`) build,
not the pure-Python fallback. The Phase 0/1 "ruff deferred / WDAC" notes
in ROADMAP are cleared accordingly. Phase 2 build proceeds.

## 2026-07-25 — Phase 2 reconciliations (§5.2 injection, real tokenizer, eval guard)
The three reconciliations mandated by the Phase 2 prompt, applied:
1. **§5.2 without a symbols table.** The symbols table is Phase 3, so exact-
   symbol injection matches against `chunks.symbol`: a candidate identifier
   `name` matches where `symbol = name` OR `symbol LIKE '%.' || name`. Identifier
   extraction keeps query tokens that have an underscore, a dot, or mixed case
   (CamelCase); plain lowercase words and all-caps acronyms are left to FTS.
   Phase 3 may migrate this to `symbols(repo_id, name)` if it proves better.
2. **Real token counter.** The ingestion pipeline now uses the embedder's
   tokenizer (`SentenceTransformerEmbedder.token_len`) for oversize-split
   decisions, replacing Phase 1's `HeuristicTokenCounter` (which is kept for the
   model-free unit tests). This supersedes the count in the 2026-07-24 heuristic-
   counter entry: httpx re-chunked 1371 → **1522** chunks (+151), because BPE
   counts code as more tokens than `len//4` estimated, tripping more §2.5 splits.
3. **Eval path guard.** `scripts/eval.py` warns loudly if any `truth.files`
   entry is absent from the `files` table, catching path-format drift before it
   silently zeroes a question.

## 2026-07-25 — Phase 2 done-when NOT met: rerank underperforms vector at hit@10
**Result.** `scripts/eval.py --mode all` on httpx @ `b5addb64` (1522 chunks,
20 questions) — the head clone landed exactly on the EVAL-pinned SHA, so there
is no version drift and the truth-file guard reported none:

| Mode | hit@5 | hit@10 |
|---|---|---|
| vector | 0.80 (16/20) | **0.85 (17/20)** |
| fts | 0.05 (1/20) | 0.05 (1/20) |
| hybrid (RRF, no rerank) | 0.80 (16/20) | 0.85 (17/20) |
| hybrid+rerank | 0.75 (15/20) | **0.80 (16/20)** |

The done-when ("hybrid+rerank hit@10 ≥ every single-signal mode") **fails**:
0.80 < 0.85. Per the ROADMAP/prompt rule, Phase 2 is **not** marked done.

**Diagnosis (from the per-question grid + the q01 smoke test).** `hybrid`
(fusion, no rerank) equals `vector` at 0.85, so RRF fusion, symbol injection and
the vector leg are all sound — the regression is entirely the cross-encoder
rerank, which evicts exactly one truth chunk (q14, `httpx._decoders.TextDecoder`)
from the top-10 that fusion had ranked inside it; at hit@5 it likewise drops one
(16 → 15). Two compounding facts:
- **Structural.** A reranker only reorders its input pool; it cannot add recall.
  So `hybrid+rerank hit@k ≤ hybrid hit@k` for any k, and the done-when can be met
  only if the rerank never pushes a top-k truth chunk past rank k. hit@10 is thus
  a metric a reranker can at best tie, never win — its value is precision at low k
  (hit@1/@3/MRR), which this check does not measure.
- **Empirical.** On this Python corpus, `bge-reranker-v2-m3` (a general-purpose
  multilingual passage reranker) mis-orders borderline conceptual-query chunks
  (q14, and the q01 smoke test put `BaseClient` above the obvious `Timeout`).
- q09/q10/q15 are missed by *every* mode including vector — retrieval-hard
  questions whose truth chunk is not even in the fused pool; not a rerank issue,
  and the kind of gap the Phase 3 agent's graph traversal is meant to close.

**Environment constraint (why this wasn't iterated to a fix here).** The dev
machine has 8 GB RAM. Loading the ~2.4 GB reranker thrashes swap; after the first
(successful, ~8 min) eval, subsequent model-loading processes block on swap I/O
at startup (sleeping, ~0 % CPU, torch never resident). `debug_search.py` and the
unit-test run could not complete for this reason — an environmental limit, not a
code defect. (Mitigation applied: `embedder.py` now imports `sentence_transformers`
lazily inside the factories, so importing `retrieval`/helpers/tests no longer
drags in torch.)

**Proposed fixes (deferred — need a host that can run the reranker + a rerun,
and a SPEC §5.3 reconciliation + DECISIONS entry before adoption):**
1. Evaluate the reranker at **hit@1/@3 and MRR**, the metrics it can actually
   improve, to judge its worth for the agent's entry points.
2. Add a **fusion floor / score blend** so the returned top-k is never worse than
   pure fusion (e.g. `final = α·norm(ce) + (1−α)·norm(rrf)`, or guarantee the top
   fusion hits survive), making the pipeline monotonically ≥ hybrid.
No fix is applied yet: applying an unvalidated algorithm change and re-declaring
the metric passed would be papering over the result.

## 2026-07-25 — FTS leg is dead (plainto_tsquery AND), not just weak; plumbing sound
Follow-up bug hunt on the FTS score (0.05, 1/20). Direct DB inspection via `psql`
(no models) rules out a plumbing bug and pins the cause on query construction:
- **Plumbing OK:** `chunks.tsv` is populated (`Timeout` chunk 722 chars,
  `urlparse` 1193, non-null), the GIN index `chunks_tsv` exists, `repo_id` typing
  is fine, the fusion FTS CTE runs.
- **Cause:** `plainto_tsquery` (SPEC §5.1) **ANDs every query term**. A full NL
  question becomes an unsatisfiable conjunction — q01 → `'request' & 'timeout' &
  'configur' & 'class' & 'defin'` matches **0** chunks; q03's five-term AND also
  0. But the key term alone matches lots (`to_tsquery('timeout')` → 295,
  `'urlparse'` → 73) and the truth chunks *do* contain their key term. So the
  FTS CTE returns 0 rows for ~every question and RRF collapses to vector-only —
  which is why `fusion == vector == 0.85` to the decimal.
- **Fix direction (not yet applied):** OR-combine the salient lexemes instead of
  ANDing. `to_tsquery('request | timeout | …')` → 845 rows (truth #21);
  `'url | pars | … | urlpars | …'` → 1227 (truth #10) — both within `FTS_K = 40`,
  so RRF would fuse a real lexical signal. This is a §5.1 change → needs a SPEC
  reconciliation + a rerun before adoption, but it is likely the highest-value
  lever (it is *why* hybrid never beats vector) and it is iterable without the
  reranker (`--mode fts` / `--mode hybrid`). Recorded as Option E in
  `docs/phase-2-rerank-review.md`. Not applied here (bug-hunt, not a tuning pass).

## 2026-07-25 — eval.py reports hit@3 and MRR alongside hit@5/@10
Added hit@3 and MRR columns to `scripts/eval.py` (hit@5/@10 unchanged). A single
first-relevant-hit rank now drives every metric: `hit@k = rank ≤ k`, `RR = 1/rank`
(0 on miss), MRR = mean RR. Rationale: a reranker optimizes the *position* of the
first relevant hit, which hit@3/MRR reward and hit@10 (a recall metric on a fixed
pool) barely reflects — so the earlier done-when tested the reranker where it is
weakest. This is a generic metric addition, not a gate change; the recall gate
(hit@10) is untouched. Metric math is unit-tested (`test_eval_parsing.py`), and
`search()` now loads the embedder lazily so `--mode fts` runs fully model-free.
