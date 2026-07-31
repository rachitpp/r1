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

## 2026-07-25 — §5.1 FTS leg: OR-combine content lexemes (fixes the dead FTS leg)
Applies the fix identified in the "FTS leg is dead" entry above (references it;
supersedes nothing else). The §5.1 FTS CTE and `_fts_leg` now build the tsquery
as `to_tsquery('english', replace(plainto_tsquery('english', $q)::text, ' & ',
' | '))` — i.e. take `plainto_tsquery`'s english-stopword-stripped, stemmed
lexemes and OR them instead of ANDing. Everything else in §5.1 is unchanged:
FTS_K=40, RRF_K=60, the vector leg, and §5.2 injection. SPEC §5.1 updated.

**Mechanism verified on q01/q03 (not fitted to them).** Direct DB checks: the OR
query for q01 is `'request' | 'timeout' | 'configur' | 'class' | 'defin'`,
matching 845 chunks with the `Timeout` truth chunk at ts_rank #21; q03 matches
1227 with `urlparse` at #10 — both inside FTS_K=40, versus **0** rows under the
old AND. Note the truth ranks equal the earlier bare-OR (#21/#10): `plainto`
already removes english stopwords, so "content lexemes, english-stopwords
removed" *is* that OR — the residual rank dilution comes from generic content
words (`class`, `function`, `implement`) that are **not** english stopwords and
so are out of scope for an english-config fix (removing them would need a custom
code stoplist — deferred, and tuning-adjacent).

**Honest expectation vs the acceptance signal.** FTS is now a real recall signal
(0 rows → hundreds), but on this benchmark it is unlikely to make `hybrid` beat
`vector` at hit@10: the vector-missed questions are semantic, and FTS surfaces
their truth files only at mid-ranks (q09 #32, q15 #14, q10 #176 — too generic),
where an FTS-only RRF contribution rarely reaches the fused top-10. The eval run
is the arbiter; numbers recorded in EVAL.md and reported as-is. Reranker mode is
left wired but un-benchmarked (8 GB host swap-stalls on the 2.4 GB model).

## 2026-07-26 — Test shadowing: retrieval targets implementation by default
**The finding.** The first eval on a host that can hold the reranker failed the
Phase 2 gate harder than the 8 GB partial run: `hybrid+rerank` hit@10 **0.75** vs
`vector` **0.85**, `hybrid` **0.80**. Fixing the FTS leg (2026-07-25) raised it
standalone 0.05 → 0.65 yet *lowered* both fused modes. Per-signal inspection of
the two regressing questions found a single cause — **test chunks systematically
outrank implementation chunks for natural-language questions**:
- **q14** (`TextDecoder`): 9 of the FTS top-10 were `tests/`; vector hit only via
  a file-level match at rank 10 exactly; RRF displaced it. Lost at *fusion*.
- **q08** (`BasicAuth`): after rerank `BasicAuth` was absent from the top-10 and
  the cross-encoder's #1 was
  `tests/test_auth.py::test_digest_auth_with_401_nonce_counting`. Lost at *rerank*.

Mechanism: tests are written in user vocabulary ("chunk", "stream", "auth") in
prose-like names and assertions; implementation is terse and identifier-dense.
Both the lexical leg and a general-purpose passage-relevance cross-encoder score
tests as more relevant to an NL question than the code implementing the answer.
On `encode/httpx`, **697 of 1522 chunks (46 %) are test code**. The FTS fix did
not cause this — it exposed it, by giving test chunks their first path into
fusion.

**The decision (product-level, not tuning).** Retrieval targets implementation by
default; tests stay in the corpus, flagged and filtered. Implemented as
flag-and-filter: `003_is_test.sql` adds `chunks.is_test`; a corpus-wide path rule
classifies at ingest (SPEC §2.6, no per-file judgment); `hybrid_search(
include_tests=False)` excludes flagged chunks from **both fusion CTEs and §5.2
injection**, filtering inside each CTE *before* the per-leg LIMIT (SPEC §5.4).
The `files` table is untouched — `read_file`/`list_directory` still see tests.
Phase 3's symbols migration becomes `004`.

**The caveat, stated plainly.** All 20 EVAL truth files are implementation, so
this change raises measured scores **by construction**. The justification is
product intent ("how does X work" should answer with the implementation, not a
test asserting it) and the generality of the mechanism — not the score. The
counterfactual remains measurable rather than asserted: `--include-tests`
reproduces the shadowed condition, and both are recorded in the same EVAL.md
block.

**Pre-registered prediction, and how it fared.** Written into
`docs/phase-2-rerank-review.md` §6bis *before* the run, with an explicit
falsifier (if `vector` gained as much as the fused modes, the mechanism story
would be weak). Measured Δ hit@10, shadowed → implementation-only:
`fts` +0.15, `hybrid` +0.15, `hybrid+rerank` +0.10, **`vector` +0.05 (smallest)**;
`fts` hit@3 +0.30 (0.25 → 0.55), the largest single delta. **Falsifier not
triggered; the mechanism holds.** One prediction missed in the favourable
direction: q09 and q15 — written off as Phase-3-only — were partly test-shadowed
and recovered. q10 remains missed by every mode.

**The gate still fails, and Phase 2 stays not-done.**
`hybrid+rerank` hit@10 **0.85 < `vector` 0.90**. Exclusion moved every mode up
but did not close the gap, because the regression relocated: `hybrid` alone is
now **0.95 (19/20)** and the cross-encoder knocks it down to 0.85 (17/20),
demoting q09 and q14 out of a top-10 that fusion had already found. Stopping here
per instruction — no further retrieval changes without sign-off.

**Notable consequence for the next decision:** `hybrid` (0.95) now clears every
single-signal mode (`vector` 0.90, `fts` 0.80), so the gate as written would pass
with rerank **off** — an option that was dead before exclusion. Recorded as
evidence, not adopted.

## 2026-07-26 — §5.2 CamelCase test requires a non-initial capital
Injection was extracting `How` from "How does httpx…" and `When` from "When I
pass auth…" — sentence-initial capitals read as identifiers (observed in the
q08/q14 debug runs). A token now counts as CamelCase only with an uppercase
letter at a **non-initial** position *and* at least one lowercase letter.
`BasicAuth`, `URLPattern`, `TextDecoder` pass; `How`/`When` and pure acronyms
(`URL`) fail. **Accepted cost:** single-capital class names (`Timeout`,
`Response`) are indistinguishable from sentence-initial words and lose §5.2
injection; the vector and FTS legs still reach them, so injection is a lost
*extra* signal, not the only path. Superseded the previous mixed-case test; SPEC
§5.2 carries the rule table.

## 2026-07-26 — Reranker ablated: optional, OFF by default
**Decision.** The cross-encoder rerank step (`bge-reranker-v2-m3`) is no longer
part of the default pipeline. `RERANK_ENABLED` (default `false`) and
`hybrid_search(rerank=…)` control it; the default returns RRF fusion order. The
model stays **wired and lazily loaded**, and `eval.py` keeps its `hybrid+rerank`
mode, so the ablation is permanently measurable rather than deleted — the same
philosophy as `include_tests` (§5.4).

**Evidence — worse-or-equal at every k and at MRR, in both corpus conditions.**
Never better on any measured cell:

| Condition | Mode | hit@3 | hit@5 | hit@10 | MRR |
|---|---|---|---|---|---|
| implementation-only | hybrid | 0.80 | **0.90** | **0.95** | **0.755** |
| implementation-only | hybrid+rerank | 0.80 | 0.80 | 0.85 | 0.722 |
| shadowed | hybrid | 0.70 | 0.80 | **0.80** | **0.617** |
| shadowed | hybrid+rerank | 0.70 | 0.75 | 0.75 | 0.604 |

There is no k at which the reranker wins — including hit@3 and MRR, the low-k
signals cross-encoders are supposed to own (review doc §4(c) predicted it might
win there; it ties at hit@3 and loses at MRR).

**Mechanism, measured not asserted** (review doc §6quater). Cross-encoder scores
on the two questions it demotes out of the top-10:
- **q09:** truth `_decoders.ZStandardDecoder` — fusion rank **4** → CE rank
  **38** (CE +0.0468). Winner: `_models.Response.aiter_bytes` (CE **+0.6666**).
- **q14:** truth `_decoders.ByteChunker.decode` — fusion rank **5** → CE rank
  **20** (CE +0.3782). Winners: `Response.iter_raw` / `iter_text` / `aiter_raw`
  (CE +0.60…+0.62), which fusion had ranked #19/#29/#21.

The reranker is not failing at the margin — it inverts a correct fusion ordering
by a wide margin, preferring chunks whose *surface vocabulary* echoes the
question over the terse implementation that does the work. This is the same
prose-beats-terse phenomenon as test shadowing (2026-07-26 entry above), acting
on implementation code.

**Option A (fusion floor / score blend) rejected.** It would restore
monotonicity by construction, but: it keeps 2.4 GB resident and ~25 min of eval
wall-clock for a component with **≤0 measured value**; it introduces a new
tuning surface (α) that must be fitted on a validation signal the project does
not have, on a 20-question benchmark where any α is over-fitted; and it
partially neuters the reranker anyway. Paying complexity to rescue a component
that never wins is the wrong trade.

**Reranker swap (Option D) deferred to the v2 backlog.** `bge-reranker-v2-m3` is
general-purpose and multilingual, applied to Python. A code-specific or smaller
cross-encoder may genuinely earn its place; that is an A/B against
`--mode hybrid+rerank`, not a Phase 2 blocker.

**Consequence — §5.2 injection rides the rerank path.** Injected chunks carry no
RRF score, so fusion-only mode has nothing to order them by; disabling rerank
also disables injection. This is the exact configuration measured at `hybrid`
hit@10 0.95. Re-attaching injection to the default path would be a new,
unmeasured pipeline and needs its own eval.

**Gate intent clarified, logged not silent.** ROADMAP's Phase 2 done-when named
a configuration (`hybrid+rerank`) rather than the intent — *the full pipeline
must not do worse than its simplest part*. It now names the **default
pipeline**. The bar was not lowered: PASS is `hybrid` 0.95 ≥ `vector` 0.90 ≥
`fts` 0.80, with dominance at every k and at MRR. Original wording preserved in
ROADMAP.

**Cross-reference.** The by-construction caveat from the test-shadowing entry
above applies to these numbers too: all 20 EVAL truth files are implementation,
so `is_test` exclusion raises every mode's score by construction. The reranker
comparison is unaffected by it — `hybrid` and `hybrid+rerank` are measured on
the *same* pool in the *same* condition, so the ablation verdict is a
within-condition comparison and holds in the shadowed condition too.

## 2026-07-26 — §5.2 injection is dormant in the shipped pipeline (by design)
**Addendum to "Reranker ablated" above.** The pipeline that measured `hybrid`
hit@10 **0.95** has exact-symbol injection **dormant**: injected chunks carry no
RRF score, so fusion-only mode has nothing to order them by, and disabling
rerank disabled injection with it. **This is accepted as design, not debt.**

**Why the benchmark cannot settle it.** EVAL's 20 questions are deliberately
phrased in *user vocabulary* — 11 of 20 have zero lexical overlap between the
question and the answer's symbol identifiers, specifically to punish keyword
matching. So the benchmark contains almost no identifier-dense queries and is
structurally incapable of measuring what injection is for. Its absence costs
nothing measurable here, and its presence could not have been credited either.

**Why the role is superseded in Phase 3.** Exact-identifier lookup becomes the
job of `get_definition` / `find_references` against the `symbols` table (§6,
migration `004`): direct index hits, no retrieval scoring involved, no ranking
to lose against. That is a strictly better answer to "where is `verify_token`
defined" than injecting a chunk into a fused pool and hoping it survives.
`search_code` keeps its own, distinct job — **semantic entry-point finding** —
which is exactly what fusion does well.

**If it is ever re-attached.** Giving injected chunks an ordering signal in
fusion-only mode is a new, unmeasured pipeline (it needs a synthetic rank or a
score blend) and requires its own eval run. Logged in the v2 backlog beside the
code-specific reranker; not a Phase 2 loose end.

## 2026-07-26 — Phase 3 reconciliation 1: symbols/edges land in `004`
`003` is `is_test` (test shadowing, above), so Phase 3's symbol graph is
`004_symbols.sql`. The `chunks.symbol_id` backfill column moves there with it.
SPEC §3's migration list reads `001 → 002 → 003_is_test → 004_symbols`.
Mechanical, recorded only so the next reader doesn't hunt for a missing `003`.

## 2026-07-26 — Phase 3 reconciliation 2: test symbols use flag-and-filter
**Decision.** Extract symbols and edges from **all** files including tests;
carry the file's §2.6 classification onto `symbols.is_test`; filter at the
tool layer, not at extraction (SPEC §6.3). `get_definition` skips test-file
definitions; `find_references` and `expand_context(direction="in")` exclude
edges whose **from**-side symbol is a test; both take `include_tests=False`
by default, mirroring `search_code`.

**Why extract-then-filter rather than skip-at-ingest.** Identical reasoning
to the Phase 2 chunk decision: a flag is reversible and measurable, a
skipped extraction is neither. Re-including tests later is a query change,
not a re-ingest. It also keeps the graph honest — the edges exist, we simply
choose not to surface them by default.

**Why the direction asymmetry.** Incoming edges from tests answer "a test
exercises this", which is noise when the agent asks "who uses this?" — and on
a well-tested repo the tests *outnumber* the real call sites, so unfiltered
incoming edges bury the answer. Outgoing edges from implementation almost
never land in tests, so filtering the `out` direction would add a branch for
no measured benefit. Filtering only where the noise actually is.

## 2026-07-26 — Phase 3 reconciliation 3: §7.4 called-by is implementation-only, capped at 8
The called-by comment block appended to tool results draws from
implementation-side incoming edges only, capped at **8** callers
(`CALLED_BY_MAX`, SPEC §12) with a `… +N more` suffix; nothing is emitted
when there are no implementation-side callers.

Two failure modes this avoids. Without the test filter, the block on a
well-tested symbol is mostly test functions — the same shadowing that cost
Phase 2 its gate, resurfacing in the agent's context instead of the
retriever's. Without the cap, a hot symbol (`Response.__init__`) emits a
caller list longer than the code it annotates, spending context budget on
data the agent didn't ask for.

## 2026-07-26 — Phase 3 reconciliation 4: provider-configurable agent model
**Decision.** `AGENT_MODEL` selects the chat provider by prefix — `gemini*`
→ `ChatGoogleGenerativeAI`, `claude*` → `ChatAnthropic`, `vertex:*` →
`ChatVertexAI` — constructed only by `app/agent/model.py`, with retry and
exponential backoff configured on the client. Tool binding stays
provider-agnostic (`.bind_tools` on whatever the factory returns). Default
is **`gemini-3.5-flash`** on Google's AI Studio free tier.

**Rationale.** Zero marginal cost per tuning iteration, which is what makes
M3's iterate-measure loop affordable at all — a phase that tunes prompts and
tool descriptions against dev questions would otherwise bill for every
attempt. `AGENT_MODEL` has been env config since Phase 0, so this is a
configuration choice the design already anticipated, not a stack change.

**Model selection, measured not assumed.** `gemini-2.5-flash` appears in
ListModels but returns 404 — *"no longer available to new users"* — so the
listing is not proof of access. `gemini-3.6-flash` returned empty content at
`max_output_tokens=32` (a thinking model consuming its whole budget before
emitting text), a real constraint on agent-loop token sizing.
`gemini-2.0-flash` 429'd on the first call. `gemini-3.5-flash` is GA rather
than preview, responds correctly, and is a pinned concrete id — an alias like
`gemini-flash-latest` would drift between runs and silently break the
within-model comparison rule below.

**Measurement rules.** The model id is recorded in **every** results block.
Stuffed-vs-agent comparisons are **within-model only** — comparing a stuffed
baseline on one model against an agent on another measures the models, not
the thesis. A one-time strong-model cross-check (Claude Sonnet, or a Vertex
Pro-class model while trial credits last) is the **pre-registered** diagnostic
if the M3 delta is ambiguous; it is not a redesign trigger.

**Vertex usage policy.** GCP service-account credentials are configured and
the `vertex:` branch is real code rather than a stub. Policy: tuning stays on
the free AI Studio key; Vertex Pro-class is for measurement runs and the
cross-check only; **default traffic never routes through Vertex.** Trial
credits are a finite measurement budget, not a tuning budget.

**Data-handling note.** The AI Studio free tier permits Google to train on
submitted data. The payload here is public repository code (`encode/httpx`)
plus benchmark questions we authored — nothing proprietary. Recorded so the
tradeoff is explicit rather than assumed; a private-repo deployment would
need a paid tier or the Vertex path, where that clause does not apply.

## 2026-07-26 — Symbol mapping: innermost containment, validated by a name probe
**Decision.** A Jedi resolution is mapped to a symbol by **innermost-span
containment**, not exact line match; the mapping is then validated by
**name agreement** (Jedi's resolved name vs the symbol's short name), with one
exemption: an **import** site landing on a **module** symbol keeps its edge
without the name check, counted separately as `module_import`.

**Why containment rather than exact line.** Jedi reports a definition's *name*
line; our spans start at the first **decorator** (SPEC §2.3). Exact-line
matching would therefore miss every decorated definition — and httpx is
decorator-dense. Containment also yields the enclosing symbol of a call site
for free, which is what the `from` side of every edge needs.

**Why the probe.** Containment is tolerant by construction, so it can attribute
a resolution to the function merely *surrounding* the true target. The probe
checks that tolerance: on disagreement the edge is dropped rather than guessed.
Measured on httpx it caught **110 call sites resolving to local variables** —
e.g. `digest` and `hash_func` inside `DigestAuth._build_auth_header`, which
without the probe become fabricated self-edges. That is the probe's proof of
value, and the reason calls stay strict.

**Why imports-to-module are exempt.** The probe validates *candidate
selection* — whether containment chose correctly among several plausible
symbols. A module-level binding (`AuthTypes = Union[...]`, `__title__`) is not
a chunked symbol, so at our granularity there is exactly **one** candidate: the
module. Disagreement there is **structural, not diagnostic** — a module's short
name is never the name of the thing imported from it. Under the strict rule
these 54 sites were dropped as stray, discarding true `_api → _types` import
edges and pushing the overall rate to 3.43%. Exempting them yields **2.30%**.

**No self-edge ban.** Recursion is a legitimate call relationship, and the name
probe already separates it from local-variable fabrication: a recursive call
agrees on name and is kept; a call to a local binding disagrees and is dropped.
A blanket ban would discard true edges to catch false ones. Pinned by two tests
(`test_recursion_keeps_its_self_edge`,
`test_local_variable_call_does_not_fabricate_a_self_edge`).

**Measured outcome table** (httpx, 4778 sites):

| kind | sites | resolved | module_import | external | unmapped | failed | stray |
|---|---|---|---|---|---|---|---|
| imports | 511 | 136 | 87 | 262 | 0 | 14 | 0 |
| calls | 4189 | 2014 | 0 | 1874 | 0 | 169 | 110 |
| extends | 78 | 67 | 0 | 11 | 0 | 0 | 0 |
| **total** | **4778** | **2217** | **87** | **2147** | **0** | **183 (3.8%)** | **110 (2.3%)** |

**Residual risk, accepted.** A Jedi import resolution that lands in the *wrong
file* passes the exemption unchallenged — the module symbol will be whatever
module Jedi named, and the probe is not consulted. This is accepted: the import
failure rate is independently measured at 3%, the exemption is narrow (import
sites only, module targets only), and `module_import` is a separate column
precisely so the unvalidated class stays visible in the audit rather than
hiding inside `resolved`.

## 2026-07-26 — Provider roles, rebalanced around tokens/day (supersedes "provider-configurable agent model")
**Supersedes** the provider-policy half of the 2026-07-26 entry
"Phase 3 reconciliation 4: provider-configurable agent model" — that entry
stands as the record of *why the factory is prefix-dispatched*; its
"tuning on AI Studio, Vertex for measurement" split is replaced here. The
factory gains a fourth branch and is then **closed**: `gemini`, `claude`,
`vertex:`, `mistral`. A fifth provider would be a config problem wearing a
code problem's clothes.

**What forced the change.** The AI Studio free tier's real limit is not the
~10–15 RPM the Phase 3 pacing rules were written against. Measured on the
first live agent run:

```
quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier
limit: 20, model: gemini-3.5-flash
```

**20 requests per day, per model.** One agent run costs up to 9 model calls
(8 tool rounds + forced answer), so the tier affords ~2 runs/day. M3 needs
~200 calls for the frozen 20 across both modes, before any tuning. `--pace`
cannot help: the limit is daily, not rate-based.

**The generalisation worth keeping.** For an agent loop, the binding
constraint is **tokens per day, not requests per day**. A request-metered
tier prices a 9-call agent run identically to a one-line chat completion, so
agent workloads exhaust it in a way the number "20/day" does not advertise.
The counter-example that makes this concrete: Groq's free tier is generous on
requests (~14.4K/day) but caps tokens near ~500K/day — plenty of *calls*, not
enough *context* for a loop that re-sends a growing message history every
turn. Mistral's free tier is metered the other way (1 RPS, 500K TPM, ~1B
tokens/month), which is the shape agent loops actually need. Evaluate a free
tier by its token ceiling and its per-request context cost, not its request
count.

**Roles.**

| Provider | Role | Why |
|---|---|---|
| **Mistral** (`mistral-medium-latest`) | **Tuning + primary measurement** | Token-metered free tier; 1 RPS pacing; same model for tuning and primary measurement keeps the within-model comparison rule intact |
| **Vertex** (`vertex:gemini-3.5-flash`) | **Confirmation measurement + strong-model diagnostic — always run** | Trial credits expire whether or not they are spent, so a diagnostic held "in reserve" is a diagnostic wasted. Continuity with the successful M2 live run |
| **AI Studio** (`gemini-*`) | **Smoke tests only** | 20 req/day/model cannot carry a measurement run; still the cheapest way to prove the factory and a key work |
| **Anthropic** (`claude-*`) | Wired, unused | No key configured; the branch stays for portability |

Fallback if `mistral-medium-latest` misbehaves in the sanity pass:
`mistral-small-latest`.

**Data handling.** Mistral's free tier requires phone verification and trains
on submitted data unless opted out; opt-out is noted as a follow-up. As with
the AI Studio note above, the payload is public repository code
(`encode/httpx`) plus benchmark questions we wrote — nothing proprietary. A
private-repo deployment would need a paid tier on any of these providers.

**M3 measurement plan** (fixed here so the runs are not re-litigated mid-phase):
1. Full frozen 20, both modes, on **`mistral-medium-latest`** — primary, same
   model the prompts were tuned on.
2. Full frozen 20, both modes, on **`vertex:gemini-3.5-flash`** — confirmation.
   The step-15 sanity pass on dev questions runs first, since it is a
   different model from the one tuned on.

Both tables go into EVAL.md with their model ids. **The thesis claim rests on
the pattern holding in both runs** — a stuffed-vs-agent delta that appears on
one model and not the other is a model result, not a retrieval result.

## 2026-07-26 — Agent traces are always persisted to disk
Every agent run writes its complete trace to `backend/var/traces/`
(gitignored) in addition to stdout, in both human and JSON form.

A model call is a metered resource — on the AI Studio tier, one of twenty per
day — so a trace lost to a terminal pipe costs real quota to recreate. This is
not hypothetical: M2's first live run produced exactly the 8-tool-call trace
the milestone report needed, and it was truncated by a `tail` on the way to
the terminal. The quota to reproduce it was already spent, and the trace is
gone (noted as a backfill gap in `docs/phase-2-rerank-review.md`). Persisting
first and printing second removes the whole class of loss.

## 2026-07-26 — What the M3 answer-hit metric can and cannot show
> ⚠️ **PARTIALLY SUPERSEDED 2026-07-27** — see "Correction: finding (a) is
> model-dependent". Two sentences below — "The agent answers it on both models"
> and "**q10** — the one falsifiable question, won on both models" — are
> **false** as standing claims: q10 is won in 3/3 controlled runs on Mistral and
> 0/3 on Vertex. The entry's actual argument (the file-level metric is
> retrieval-bound; 19 of 20 questions cannot discriminate) is **unaffected and
> still correct**. Text left as written.

Recorded because the aggregate numbers (0.95 / 1.00) invite a stronger claim
than the evidence supports.

**The file-level metric is largely retrieval-bound.** `answer-hit` scores 1 if
the answer carries one validated citation whose *file* is in `truth.files`. The
stuffed baseline is handed the top-10 chunks from a pipeline whose hit@10 is
**0.95** — so "the right file was in the context window" and "the model
assembled an answer across files" score identically. The baseline scored
exactly 0.95 on both models, which is the retrieval number, not a coincidence.

**Consequence: 19 of 20 questions had no discriminating power.** Only a
question retrieval *cannot* reach can separate the modes under this metric. On
the frozen 20 there is exactly one: **q10**, missed by every retrieval mode in
every Phase 2 condition. The agent answers it on both models; the stuffed
baseline misses it on both.

**Therefore the thesis rests on two things, and neither is the aggregate:**
1. **q10** — the one falsifiable question, won on both models.
2. **The mechanism** — graph-tool use, cross-tabulated against correctness.

It does **not** rest on 0.95 vs 1.00. Anyone quoting those two numbers as the
result is quoting the retrieval pipeline's hit@10 twice.

A symbol-level metric was added to address this (below): it asks that the
answer demonstrably *use* the truth symbol, which a top-10 pool cannot supply
by accident the way it supplies a filename.

## 2026-07-26 — The flow tier is answerable from retrieval alone
> ⚠️ **AMENDED 2026-07-27** — see "Correction: the flow-tier 'every cell' claim".
> "All five flow questions scored ✓ in every cell" was true of the four blocks
> that existed when this was written, and is **false across all ten**: one
> controlled Mistral run misses **q17**. The entry's conclusion — the flow tier
> does not discriminate retrieval from traversal — is **unaffected**. Text left
> as written.

The Phase 3 prompt predicted the agent's advantage would appear on the **flow**
tier (q16–q20) — "what happens when…" questions whose answers span files, which
`expand_context` exists to assemble.

**It did not.** All five flow questions scored ✓ in every cell: both modes,
both models. Stuffing ten top-ranked chunks into one context window answers
them.

**The finding is about the benchmark, not the agent.** When retrieval hit@10 is
0.95, a ten-chunk pool routinely contains every file a "flow" question needs,
so the question stops requiring assembly. These questions discriminate between
*retrieval* configurations (they did so in Phase 2) but not between retrieval
and traversal.

**v2 backlog item:** design cross-file questions a single top-10 pool cannot
satisfy — e.g. answers requiring a symbol whose file never ranks for the
question's vocabulary, or requiring three files where the pool holds two. q10 is
the accidental existence proof that such questions discriminate; the benchmark
needs them by construction rather than by luck.

## 2026-07-26 — Two configuration bugs behind the recorded model ids
Both surfaced during the M3 measurement runs; recorded so the model ids in the
EVAL.md result blocks are traceable to what actually executed.

**1. Vertex credentials in `.env` never reached `google-auth`.**
`google-auth` reads `GOOGLE_APPLICATION_CREDENTIALS` from `os.environ`, but
pydantic-settings loads `.env` into the `Settings` object and does not export
it. A correctly-configured path therefore raised `DefaultCredentialsError`. The
bug was invisible to ad-hoc probe scripts, which export the variable by hand —
it only appeared through the application's own config path. Fixed by adding the
field to `Settings` and bridging it in the `vertex:` branch of the model factory
(without clobbering a real environment variable).

**2. `vertex:gemini-3.5-flash` does not exist.** The M3 plan named it, but
Vertex's model catalogue differs from AI Studio's. Probing the project found
`gemini-2.5-flash` and `gemini-2.5-pro` available and `gemini-2.0-flash`,
`gemini-3-pro-preview`, `gemini-flash-latest` absent. **The confirmation run
therefore executed on `vertex:gemini-2.5-flash`** — same Flash tier, closest
continuity with the M2 live run. Note `gemini-2.5-flash` is reachable on Vertex
even though AI Studio reports it "no longer available to new users"; provider
catalogues are not interchangeable and a model id must be verified per provider.

## 2026-07-26 — Phase 3 go/no-go checkpoint: GO, narrowly scoped
**Ruling: GO.** Phase 3 passes. The thesis — *retrieval finds entry points;
graph traversal finds the answer* — is **supported**, and the scope below is
the claim. It is deliberately not rounded up; the README carries this exact
three-tier framing.

### (a) STRONG — the agent answers what retrieval cannot
> ⚠️ **SUPERSEDED 2026-07-27** — see "Correction: finding (a) is
> model-dependent, not 'every run, both models'". This tier is downgraded to
> **MODEL-DEPENDENT**: q10 is answered in 3/3 controlled runs on Mistral and
> 0/3 on Vertex. The text below is left exactly as written — the record that
> the claim was made and then corrected is part of the log.

**q10** is answered by the agent and missed by the stuffed baseline across
**four independent runs on two models** (`mistral-medium-latest`,
`vertex:gemini-2.5-flash`; file-level and symbol-level metrics). q10 is the
sole question missed by **every** retrieval mode — vector, fts, hybrid,
hybrid+rerank — in **both** corpus conditions throughout Phase 2. **q14** joins
it on Vertex under the symbol-level metric.

This is the falsifiable core: a question retrieval provably cannot reach, which
the graph reaches, repeatedly, on models from different providers.

### (b) MODERATE — the agent leads at symbol level, directionally
Symbol-hit, agent vs stuffed: **+2 on Mistral** (0.85 vs 0.75), **+1 on
Vertex** (0.85 vs 0.80). The lead replicates in sign on both models, and the
stricter metric recovered discrimination the file-level metric masked.

**But both margins sit inside the ±2-question variance measured on the dev
set** (identical configuration scored 7/7 then 5/7 at `temperature=0`). The
direction is consistent; the magnitude is not resolved by single runs. Stated
as directional, not conclusive.

### (c) NOT SUPPORTED — graph-tool use as a causal mechanism
The cross-tabulation of graph-tool use against symbol correctness **did not
replicate**:

| | Mistral | Vertex |
|---|---|---|
| used a graph tool → symbol-hit | 7 / 7 | 10 / 12 |
| no graph tool → symbol-hit | 10 / 13 | 7 / 8 |

Mistral's 7/7 looked like the mechanism made visible. Vertex — which uses graph
tools far more heavily (53% of calls vs Mistral's 14%) — shows 83% with versus
88% without, i.e. no advantage or a slight inversion. On 8 and 12 questions
either reading is noise.

**This remains an observation, not a claim.** The split is also not randomised:
the agent chooses when to reach for a graph tool, so any correlation may
reflect which questions it judges to need one. The mechanism is *plausible and
consistent* with (a); it is *not demonstrated*.

### What is explicitly NOT claimed
- Not that the agent beats stuffing in aggregate. File-level scores tie or
  differ by one, and that metric is retrieval-bound (see the metric-limitation
  entry above).
- Not that flow questions favour the agent. q16–q20 tie in every cell, against
  the phase prompt's stated expectation.
- Not that graph-tool use causes correctness. See (c).

## 2026-07-26 — Phase 3 FINAL RESULT (checkpoint closed)
The definitive Phase 3 finding, superseding the interim scoping in the
"go/no-go checkpoint: GO" entry above. **Phase 3 passes.** Three claims,
ranked by the strength of their evidence. This exact framing is the README's
core claim.

### (a) STRONG — graph traversal reaches what retrieval cannot
> ⚠️ **SUPERSEDED 2026-07-27** — see "Correction: finding (a) is
> model-dependent, not 'every run, both models'". "Every run on both models" is
> **false**: it was read off the pre-temperature-pin runs, and in all three
> controlled temperature-0 Vertex runs the agent misses q10. Text left as
> written.

The agent answers **q10** in **every run on both models**; the stuffed
baseline misses it in every run. q10 is the sole question missed by *every*
retrieval mode — vector, fts, hybrid, hybrid+rerank — in *both* corpus
conditions across the whole of Phase 2. **q14** behaves the same way on Vertex
under the symbol-level metric.

The thesis holds on the falsifiable questions: where retrieval provably cannot
reach, the graph does.

### (b) MODERATE, directionally stable — symbol-level lead
Agent leads the stuffed baseline at symbol level in **6 of 6 runs across two
model families**:

| Model | run margins | agent mean | stuffed |
|---|---|---|---|
| `mistral-medium-latest` | **+5 / +4 / +2** | 0.93 (0.85–1.00) | 0.75 |
| `vertex:gemini-2.5-flash` | **+1 / +1 / +2** | 0.87 (0.85–0.90) | 0.80 |

**The sign is stable; the magnitude is noisy.** Mistral spans 0.85–1.00 across
identical configurations. State it exactly that way — six positive runs is
evidence of direction, not of effect size.

### (c) NOT SUPPORTED — graph-tool use does not predict correctness
At identical temperature 0, the two models produce **perfectly inverted**
cross-tabulations:

| | with graph tool | without graph tool |
|---|---|---|
| Mistral (3 runs) | 20 hit / **0 miss** | 36 hit / 4 miss |
| Vertex (3 runs) | 28 hit / **6 miss** | 24 hit / **0 miss** |

Every Mistral miss came from a run using *no* graph tool; every Vertex miss
came from a run that *did*. Opposite directions at the same temperature.

**This is a selection effect.** The agent chooses when to reach for a graph
tool, and it reaches on the questions it finds hard — which differ by model. It
is not the mechanism becoming visible.

**Retraction recorded.** An earlier single-run Mistral cross-tab of **7/7**
was reported as the mechanism made visible. It did not replicate on Vertex, and
the repeat diagnostic showed the inversion above. The 7/7 was an artifact of one
model and one run. Caught, retracted, and kept here so it is not re-derived.

### Methodology correction — a controlled comparison, not an invalidation
Temperature was pinned to 0 on Mistral but left at the **provider default of
1.0** on Gemini and Vertex. Every cross-model comparison before the repeat
diagnostic therefore ran temperature-0 against temperature-1.0.

All four providers are now pinned to 0 in `app/agent/model.py`, with this
history in the factory docstring so it cannot recur by omission.

**The earlier results are not invalidated** — the corpus, the frozen questions,
the retrieval pipeline, and the metrics were unchanged, and the qualitative
findings (q10, the flow-tier tie, the metric limitation) all reproduce. They
simply were not like-for-like across providers. **The six repeat runs are the
first controlled cross-model comparison in the project**, and every conclusion
above rests on them.

## 2026-07-27 — Phase 4 reconciliation 1: `linking` joins the §10 state machine

`queued → cloning → parsing → linking → embedding → ready | failed`.

The Phase 3 symbol pass runs after parsing, while the clone is still on disk,
and on the benchmark repo it is **34 s of a ~7 min ingest** — 30–40 % of wall
time on a mid-size repo. Folded into `parsing` it is invisible: a user watching
"parsing" not move for half a minute concludes the job is wedged.

No migration: `repos.status` is `TEXT`, and the frontend does not exist yet, so
this is the cheap moment to widen the enum. SPEC §3 comment and §10 updated.

## 2026-07-27 — Phase 4 reconciliation 2: one `run_ingest`, two callers

`app/ingest/pipeline.py::run_ingest(repo_id)` is the whole pipeline — clone →
filter → parse → symbols → embed → backfill — with delete-and-replace at the
start and workdir cleanup in `finally`. The ARQ task and the ingest CLI both
call it; the CLI is now argument parsing plus the stats block.

Phase 2/3 grew this inline in `cli.py`. Copying it into the worker would have
left two pipelines that drift, and the CLI is where every ingest decision was
actually proved out. Ordering changed in one respect: symbols are extracted
**before** embedding (they only need the clone), while the `symbol_id` backfill
runs **after** the chunk insert (it joins chunks to symbols). So a run that dies
during embedding still leaves a complete graph, and the states stay in pipeline
order.

The repo row now exists *before* the pipeline runs (`POST /repos` or the CLI
creates it `queued`), so `upsert_repo` — which invented the row from clone
metadata — is gone. `head_sha`, `default_branch`, and `name` are written after
the clone resolves them.

## 2026-07-27 — Phase 4 reconciliation 3: the API warms the embedder at startup

SPEC §4 says models load once per process at startup. Phase 2 made the load
lazy as a workaround for an 8 GB host that swap-thrashed on the reranker; that
host is gone (HANDOFF "Environment situation — RESOLVED"), and **this note
supersedes the lazy-load entry**: the API lifespan now calls `get_embedder()`.

Every chat request runs `search_code`, so with lazy loading the first question
of a session paid ~18 s of model load *inside its own SSE stream*, where it
looks like a hung agent. Lazy behaviour remains for CLI and test paths, and the
warm-up is wrapped in try/except — a cold model must not make the API unbootable.

## 2026-07-27 — ARQ poll delay is 2 s, for the Redis command budget

ARQ polls Redis every **0.5 s** by default: ~172 800 commands/day per worker,
**~5.2 M/month**, with the worker completely idle. Managed free tiers are
metered in commands (Upstash: 500 K/month; Redis Cloud: 30 M/month soft) — 0.5 s
polling exhausts an Upstash free tier in **about three days** of doing nothing.

`poll_delay = 2.0` costs up to two seconds before a submitted repo starts
cloning, against a job that runs for minutes, and brings an idle worker to
~43 200 commands/day (~1.3 M/month). Recorded here as a **Phase 6 deploy
consideration**: a 24/7 hosted worker needs this number chosen against whatever
tier it lands on, and if the queue moves to a per-command plan the honest fix is
a blocking pop, not a longer poll.

Redis for local development is Redis Cloud (free tier), reached over a DSN in
`backend/.env`; `docker compose up -d redis` remains the offline path.

## 2026-07-27 — SSE `tool_result` payloads are an allowlist, not a filter

§9 says summaries and locations only. `app/api/tool_events.py` therefore names
the fields it copies out of each tool's JSON — `file_path`, `start_line`,
`end_line`, counts, qualnames — and an unrecognized payload shape gets a generic
summary and no locations. It never *removes* `code`/`content`/`preview`/`tree`,
because a filter has to be updated to stay correct and an allowlist does not:
the failure mode of a forgotten filter is leaking a full file body on every tool
call, on every question.

Measured by replaying the exact tool calls of the recorded live run
(pallets/itsdangerous, `search_code` → `expand_context` → `read_file`): 6038
bytes of tool JSON went to the model, 1305 bytes went over the wire as
`tool_result` events — **4.6×**, all of the difference being code the viewer can
fetch from `/files` when a step is actually expanded. The ratio grows with repo
size, since `search_code` previews and `expand_context` bodies dominate the
model-side payload.

## 2026-07-27 — Text-delta granularity is the provider's, and both cases are handled

The Phase 3 graph's `model_node` calls `ainvoke`, not `astream`, so the
expectation was one `text` event per assistant message. In practice
`astream_events` reports token-level `on_chat_model_stream` chunks anyway for
providers whose client streams internally — a live Mistral run emits ~70 `text`
deltas for one answer.

`chat_stream.py` handles both without touching the graph: stream chunks are
forwarded as they arrive, and the whole-message fallback at
`on_chat_model_end` is suppressed for any run that already streamed. A provider
that does not stream degrades to one `text` event per message instead of
double-sending the answer.

## 2026-07-27 — Phase 5 reconciliation: custom `useRepoChat`, not the Vercel AI SDK

CLAUDE.md's stack row named the AI SDK's `useChat` before the §9 event schema
was frozen. The SDK speaks its own stream protocol (`data:`-framed parts with
SDK-defined types); ours is seven named SSE events that map 1:1 onto UI state.
Adapting means either a server-side translation of §9 into the SDK's protocol
or a client-side re-parse of what the SDK abstracted — a translation layer in
either direction, for zero benefit, plus a dependency whose protocol changes on
its own schedule.

Shipped instead: `lib/sse.ts`, an incremental parser over
`fetch` + `ReadableStream` (~60 lines: `event:`/`data:` lines, multi-line data,
CRLF, comment keep-alives, buffering across arbitrary chunk boundaries —
unit-tested by feeding the recorded §9 wire format one byte at a time), and
`hooks/use-repo-chat.ts`, which dispatches events into
`steps[] / answer / citations / status / toolCallsUsed`.

Two rules are load-bearing in the hook: **text deltas always accumulate** (§9
grants no granularity — Mistral streams ~70 token deltas, a non-streaming
provider sends one whole-message delta; DECISIONS 2026-07-27 "Text-delta
granularity"), and a **409 mid-chat is a state, not an error** — the repo
regressed to not-ready, and the UI redirects to the status page instead of
rendering a failure bubble.

CLAUDE.md's stack row is updated. Revisit only if the app ever needs the SDK's
actual value (multi-provider client-side streaming, resumable streams).

## 2026-07-27 — `failed` repos retry through `POST /repos`, not a new endpoint

The Phase 5 UI needs a Retry button, and the Phase 4 API already had the
semantics: `POST /repos` on any known URL whose status is not in-flight resets
the row (`start_ingest`: status → `queued`, error cleared, counters zeroed) and
re-enqueues, returning 200. `failed` was never in `IN_FLIGHT_STATUSES`, so no
backend code changed in Phase 5 — the pre-authorized exception turned out to be
a test (`test_post_repos_failed_repo_is_re_enqueued`) pinning the behaviour the
button depends on, including `error: null` in the response so a stale failure
message cannot linger in the UI.

## 2026-07-27 — Chat transcript in sessionStorage, per repo, completed exchanges only

The transcript persists to `sessionStorage` keyed `chat:{repoId}` so a refresh
restores the conversation. Deliberate limits: **completed exchanges only** — an
in-flight stream is cut by refresh and not resumed (resumable streams need
server-side session state, which v1's single-user scope does not justify) — and
**session** storage, not local: a conversation is working memory, not a
document, and two tabs on the same repo diverging is acceptable where silently
sharing state between them is confusing. Writes are wrapped in try/catch;
quota or private-mode failure degrades to refresh-loses-history, never a broken
app.

## 2026-07-27 — Playwright (dev-only) for live browser verification

Phase 5's done-when is defined in a browser — a streamed timeline, a citation
click landing on highlighted lines, a clean console — and this Codespace ships
no Chrome, so "verify live" had no vehicle. With the human's approval (CLAUDE.md
rule 11), `playwright` was added to the frontend's devDependencies and headless
Chromium drove the whole verification script: submit→ready on a fresh repo,
the httpx chat with a live model, the citation-to-highlight check (DOM line text
compared against `/files` content), the sessionStorage refresh, the Retry
re-enqueue, and both error paths — with console errors captured throughout.

It is a verification tool, not part of the app: nothing in `src/` imports it,
and no test harness depends on it. If a Phase 6 e2e suite ever wants it, that
is a new decision.

## 2026-07-27 — Naive chunking baseline: a scoped exception to the AST rule
Phase 6's headline table needs a naive-chunking column, and CLAUDE.md hard rule
4 forbids raw character splits. Rather than soften the rule, the exception is
carved out and bounded (SPEC §2.7, `app/ingest/naive.py`).

**Parameters, fixed a priori:** `NAIVE_CHUNK_CHARS = 1_000`,
`NAIVE_CHUNK_OVERLAP_CHARS = 100` (SPEC §12). These are the common off-the-shelf
window/overlap defaults. They were chosen **before** any measurement and were
not adjusted afterwards — a baseline tuned until it loses is not evidence, and
the whole point of the comparison is that a reader can trust it.

**Scope of the exception:**
- reachable only via `--strategy naive` on the ingest CLI; `POST /repos` and the
  ARQ worker have no path to it
- the baseline corpus is its own `repos` row. `repos.url` is UNIQUE (§3), so it
  is keyed `<url>#naive` / named `<name>@naive`; the fragment is stripped before
  cloning and never reaches git. The AST corpus at the pinned SHA is untouched —
  verified 825 impl / 697 test both before and after the baseline ingest
- `build_graph` is forced off: the symbol graph is an AST product, so a
  "naive + graph" corpus would be a baseline of nothing
- citations are **not** part of the carve-out. Every window carries
  `file_path`/`start_line`/`end_line` (rule 5), which is what makes an
  answer-level comparison possible at all

**What the comparison actually compares.** The §2.4 header is an AST product
(symbol, kind, signature, imports). The naive header keeps the shape but leaves
those fields empty or synthetic, so the measured contrast is *AST chunking plus
its enrichment* versus *fixed windows* — the whole strategy, not boundaries in
isolation. Holding the header constant to isolate boundaries alone is a v2
ablation, and the README says so rather than implying a cleaner experiment than
was run.

**Chunk counts.** Naive produced **657** chunks (327 impl / 330 test) against
AST's **1522** (825 / 697) on the same 60 files. Naive chunks are therefore
roughly 2.3× larger on average, which if anything favours the baseline on
hit@k: a bigger window is more likely to contain a ground-truth symbol by
accident. Worth stating, because it means a naive loss is not a chunk-size
artifact.

## 2026-07-27 — Phase 6 finishes local-first; the deploy is documented, not stood up
Phase 6's ROADMAP text lists a live deployment. It ships as `docs/DEPLOY.md`
instead, and the phase closes without a URL.

**Why.** The remaining risk in a live deploy is concentrated in one place: the
ARQ worker is a second always-on process, not a flag on the API, and platform
surprises there are open-ended in a way the rest of the stack is not. Against
that, the marginal value of a URL for this project is small — it is a portfolio
piece whose differentiator is measured, honestly-scoped results. A reviewer who
clones the repo, runs it from the README, and reads the three-tier finding is
better served than one who clicks a link. A repo a stranger cannot run is a
dealbreaker no URL fixes; a missing URL is an afternoon's work whenever it is
actually wanted.

**What this is not.** It is not "deploy is blocked" or "deploy was attempted".
`docs/DEPLOY.md` is written to be followable and says plainly, at the top and in
a Known Gaps section, that it has never been executed. The ROADMAP done-when box
is ticked against that guide plus a working local run, with the deferral noted
as a choice.

## 2026-07-27 — The naive baseline is a null result at hit@k, and the README says so
The Phase 6 comparison table is filled. **Fixed 1000-character windows match AST
chunking on the headline retrieval metric** — hybrid hit@10 0.95 in both columns,
vector 0.90 in both — and naive is *ahead* on FTS (0.90 vs 0.80). Naive trails
only on hit@5 (0.80 vs 0.85) and MRR (0.734 vs 0.752).

**The run.** `eval.py --mode vector,fts,hybrid --repo c7815d7b…` (2m48s)
reproduced the pre-existing EVAL.md block exactly, mode for mode and question
for question. HANDOFF had recorded that run as "killed mid-flight"; it had in
fact completed, and only the README text lagged. Both corpora re-verified in the
database at the time of the re-run: AST 1522 (825/697) at the pinned
`b5addb64`, naive 657 (327/330).

**Why this is reported rather than fixed.** The window parameters were fixed a
priori (see the entry above) and were not touched after the result was seen.
Naive chunks are ~2.3× larger, and hit@k asks only whether a ground-truth symbol
landed *somewhere* in a retrieved window — a metric that structurally rewards
bigger windows. Resizing the baseline until it loses would make the table
worthless, which is the one thing this project's headline cannot afford.

**What it changes about the claim.** Nothing that was actually claimed, but it
removes a claim a reader might have expected. The case for AST chunking on this
benchmark is **not** retrieval hit-rate. It is (1) the symbol graph, which fixed
windows cannot produce at all — `build_graph` is forced off for the naive row —
and (2) the answer-level and symbol-level numbers the agent rests on, which are
measured only on the AST corpus. The naive corpus was never given an
answer-level run; that measurement is available via `answer_eval.py --repo` and
was simply not spent. Stating that gap is cheaper than implying it isn't there.

**Consistent with the Phase 3 finding**, which already refused to quote
file-level aggregates as the result for the same reason: a metric a retrieved
pool can satisfy by accident does not discriminate. hit@k on 60 files of httpx
is now a second instance of that.

## 2026-07-27 — Correction: finding (a) is model-dependent, not "every run, both models"
Filling the Phase 6 comparison table required writing a q10 row, and checking it
against every answer-level block in EVAL.md showed the standing claim is wrong.

**What was claimed.** "q10 is answered by the agent and missed by the stuffed
baseline in every run, on both models" — the STRONG tier of the Phase 3 finding,
repeated in README, ROADMAP, and HANDOFF.

**What the blocks actually show** (`| q10 |` row of every agent block):

| Model | q10 hit | of runs | detail |
|---|---|---|---|
| `mistral-medium-latest` | 5 | 5 | every run, including all three controlled |
| `vertex:gemini-2.5-flash` | 2 | 5 | **both hits pre-temperature-pin (default 1.0)** |

In all **three controlled Vertex runs at temperature 0** — the six repeats that
this project itself calls its first like-for-like cross-model comparison — the
agent **misses** q10.

**How it hid.** The claim was written when the paired `stuffed` vs `agent`
blocks were the newest evidence, and in those Vertex does hit q10. The three
controlled Vertex repeats were appended afterwards, as part of the very run set
added to fix the uncontrolled-temperature problem, and the prose was never
re-derived from them. The same defect as the retracted 7/7 cross-tab: a reading
taken once and then carried forward instead of recomputed.

**Correction.** Finding (a) is downgraded from **STRONG** to **MODEL-DEPENDENT**
in all four documents. The canonical wording, used verbatim in README, ROADMAP,
HANDOFF, and here so the four cannot drift again:

> **q10 — the only question no retrieval mode reaches in any condition — is
> answered by the agent in 3/3 controlled temperature-0 runs on Mistral and 0/3
> on Vertex (0/2 distinct results: two of Vertex's three blocks are
> byte-identical and probably a double-append), or 5/5 and 2/5 across all runs,
> both Vertex hits pre-temperature-pin: graph traversal can reach what retrieval
> cannot, demonstrated on one model family and not reproduced on the other.**

Any future change to this claim edits all four sites or none.

**What is unaffected.** Finding (b) still holds exactly as stated — the six
controlled runs give a symbol-level lead in 6/6 (Mistral +5/+4/+2 vs baseline
0.75; Vertex +1/+1/+2 vs 0.80), and Vertex's q10 misses are already inside its
0.85/0.85/0.90. Finding (c) is unchanged. That (b) survives while (a) weakens is
the expected shape: the aggregate lead was always the more replicated result,
and the single-question proof was always the more fragile one.

## 2026-07-27 — Two Vertex eval blocks are byte-identical: probable double-append, unresolved
While re-deriving finding (a), the eighth and ninth answer-level blocks in
EVAL.md — both `vertex:gemini-2.5-flash`, both part of the "three controlled
repeat runs" — were found to be **identical line for line**: same summary row
(file 0.95, symbol 0.85, cited 1.00, tools 3.5/9), same `Model calls: 89`, same
20-row per-question grid, same graph-tool cross-tab.

**Why this probably is not determinism.** The third Vertex block differs
(symbol 0.90, tools 3.7/9, 94 calls) under the same configuration, so Vertex is
**not** deterministic at temperature 0. Two runs agreeing on every one of those
figures — including the exact model-call count — is far more consistent with the
same run's block being appended twice than with a reproduced result.

**Consequence for the record.** Vertex's controlled evidence is **2 distinct
results, not 3**. Every statement of the q10 tally now carries "0/2 distinct"
alongside "0/3 runs" (see the canonical sentence in the correction entry). The
direction is unchanged — q10 is missed in both distinct Vertex results — but the
sample is smaller than the block count suggests.

**Unresolved, and left that way.** Distinguishing a duplicate append from a
genuinely reproduced run needs the run logs or agent traces from those two
invocations, which are not available here. Guessing would be worse than the
caveat. Recorded so nobody counts three Vertex runs again.

**Measurement-hygiene lesson for any future eval work.** `answer_eval.py` and
`eval.py` both append a block with no run identity and no idempotency check, so
a duplicate append is invisible and a re-run is indistinguishable from a
double-write. Any future eval work should stamp each block with a **run id**
(uuid), the **git SHA**, the **config hash**, and a **wall-clock start time**,
and refuse to append a block whose run id already exists. This is the same class
of defect as the uncontrolled temperature and the carried-forward 7/7 cross-tab:
the measurement was trustworthy, the *bookkeeping around it* was not.

## 2026-07-27 — Correction: the flow-tier "every cell" claim
Found by the same re-derivation that broke finding (a), and recorded separately
because it is a distinct claim.

**What was claimed** (DECISIONS 2026-07-26, "The flow tier is answerable from
retrieval alone"): "All five flow questions scored ✓ in every cell: both modes,
both models."

**What the blocks show.** True of the four blocks that existed when it was
written. **False across all ten:** the third controlled Mistral run misses
**q17**. Every other flow cell in every other block is ✓ — 49 of 50 agent cells
and 20 of 20 stuffed cells.

**What survives.** The entry's conclusion is untouched: the flow tier does not
discriminate retrieval from traversal, and a single q17 miss in one Mistral run
is noise of exactly the magnitude already documented for that model (±2
questions on a 7-question dev set). The v2 backlog item — design cross-file
questions a top-10 pool cannot satisfy — stands unchanged.

**Same failure mode, third instance.** A reading taken once and carried forward
rather than recomputed when new blocks landed. The first two were the retracted
7/7 cross-tab and finding (a). All three would have been caught by re-deriving
every claim from the blocks at the end of each measurement round, which is now
the practice: **no claim in these documents is quoted from prose; each is
recomputed from EVAL.md before it ships.**

## 2026-07-28 — Landing-page visual pass: design tokens, a typeface, and a hero

Phase 6 is a feature freeze ("Do not: add features"), and this stays inside it:
no new screens, no new API surface, no new backend behaviour. What changed is
how the existing three screens look. Recorded because it edits the shipped
design tokens, which every page inherits, and because it adds a build-time
network dependency the repo did not have.

**Why.** The landing page rendered as an internal CRUD list: six near-identical
white slabs, the system font stack, one green pill for colour, and no statement
of what the product does. The measured evidence the project is built around —
AST chunking, the symbol-graph agent, clickable `file:line` citations — was
invisible above the fold, which is exactly what Phase 6's "legible to a stranger
in 60 seconds" goal asks for.

**What changed, in tokens.**
- `--primary` moved from near-black (`222 47% 11%`) to indigo (`243 75% 59%`),
  and `--ring` with it, so every interactive affordance shares one accent. This
  is inherited: the chat page's user bubbles and active citation chips are now
  indigo too. That consistency is the point.
- `--background` is a hair off-white (`210 20% 98%`) and a new `--card` is pure
  white. **`Card` had always rendered `bg-card`, but the token was never
  registered in `tailwind.config.ts`** — cards were transparent and only looked
  white because the page behind them was. Registering it is the fix that lets
  the surface carry a tint.
- `--radius` 0.5rem → 0.75rem, plus a `rounded-xl` step.

**Typography — the one trade-off worth naming.** `next/font/google` (Inter +
JetBrains Mono) is built into Next, so **no package was added** and rule 11 is
untouched. But it fetches the font files at build time: a **first** build on a
machine with no network now fails where it previously succeeded. Subsequent
builds hit `.next/cache`. Accepted — this repo already needs the network to
`uv sync`, pull the HF embedding model, and reach Postgres/Redis — but it is a
real change to the offline story and `next/font/local` with committed woff2
files is the escape hatch if that ever matters.

**Rejected: GitHub avatars via `next/image`.** Owner avatars come from
`github.com/{owner}.png` through a plain `<img>` with an `onError` fallback to a
monogram tile. `next/image` would need `remotePatterns` config and leans on
`sharp`, which this project lists under `ignoredBuiltDependencies`. One lint
rule is suppressed on that line with a reason; the fallback means an offline or
404 owner degrades to a tile rather than a broken image.

**Header height.** `h-12` → `h-14`, which required updating the chat page's
`h-[calc(100vh-3rem)]`. It is now `calc(100vh-3.5rem-1px)` — the extra pixel is
the header's bottom border, whose omission had been leaving a 1px page
scrollbar on the chat route since Phase 5.

**Verified.** `pnpm build`, `pnpm lint`, `npx tsc --noEmit` clean; 16 vitest
tests green; chat page still 181 kB first load (unchanged from the Phase 5
record), landing 130 kB. Screenshotted at 1440px and 390px on all three routes
via Playwright against the running api+worker: zero console errors, zero
horizontal overflow, and the chat route's vertical overflow now measures 0.

## 2026-07-28 — Chat-page pass: markdown answers, a following code pane, and stop/clear

Companion to the landing-page entry above, same reasoning about the Phase 6
freeze: no new screens and no new API surface. Two items are small behaviour
changes rather than pure styling, and are named as such below.

**The defect that mattered most.** Answers rendered as `whitespace-pre-wrap`
plain text while the model emits markdown, so every answer showed literal
`**bold**` and `` `backticks` ``. Fixed with a **hand-written parser**
(`lib/markdown.ts`), not a library — **no dependency added** (rule 11).

Three reasons a library was the wrong tool here:
1. Answers interleave `[path:start-end]` markers (SPEC §7.5) that must become
   interactive chips *inside* the prose, so the citation split has to happen
   in the same pass as the markdown one.
2. The text streams token by token, so every intermediate render is
   half-written markdown. The parser leaves anything unmatched literal, which
   is precisely the degradation a streaming renderer needs.
3. **`_underscore_` emphasis is deliberately not supported.** In a Python
   assistant, `__name__` would render as emphasised "name". A rare literal
   underscore pair is a far cheaper failure than mangling every dunder.
`lib/markdown.test.ts` covers exactly these: dunders, markdown inside inline
code, malformed ranges, unterminated fences, and half-written markers.

**The right pane no longer starts dead.** It was ~600px of "Click a citation…"
until the first click. It now follows the agent — each `tool_result` opens the
file just read, and the finished answer opens its first validated citation —
and yields permanently to the viewer's own click for that exchange. *Behaviour
change, deliberate.*

**Auto-follow is a wide-viewport behaviour only (≥1024px), and that limit was
learned the hard way.** Shipped unconditionally, it was reported as the page
looking "zoomed". Below `lg` the viewer is not a side pane but a full-screen
sheet, so auto-opening one threw a code overlay across the whole page the
moment an answer landed, with unwrapped code needing horizontal scrolling
inside it. Opening a *pane* unasked is helpful; opening a *sheet* unasked
hijacks the screen. Narrow screens keep tap-to-open, and wrapping now defaults
on wherever the pane is narrow. **The general rule: an affordance that is
ambient at one breakpoint can be modal at another, and auto-behaviour has to be
scoped to the breakpoint it was designed for.**

**Stop and New chat.** `useRepoChat` had held an `AbortController` and a
`clear()` since Phase 5 with no UI attached to either. Both are now buttons.
Stop keeps the partial exchange rather than discarding it (`stopped: true`),
which required distinguishing the three reasons a stream aborts: viewer stop
(keep), replacing question (discard), unmount (discard). *Behaviour change,
deliberate.*

**Highlighting is a gutter accent, not a background wash.** A citation can
legally span a whole file — the measured case was `main.py:1-215`, where the
old `bg-amber-100/80` washed all 215 lines and emphasised nothing. A 2px
gutter bar plus a 6% tint scales from a 4-line citation to a whole-file one,
and the start line stays distinctly marked at either extreme.

**Mobile was broken, not merely cramped.** The viewer was a fixed `h-80` strip
*below* the composer, so a phone showed a squeezed conversation, then the
input, then 320px of code that could not be dismissed. It is now a sheet that
slides over on citation click. One `CodeViewer` instance serves both layouts
(`fixed` below `lg`, `static` at `lg`), so nothing is mounted twice.

Also: step timings measured client-side between `tool_call` and `tool_result`
(§9 carries none); per-tool icons; a finished timeline collapsing to
`3 tool calls · list_directory → read_file → read_file · 703ms`; `read_file`'s
summary no longer printed next to the identical chip it duplicates (backend
`summarize_tool_result` returns exactly `path:start-end` for it); Sources
listing only citations the prose did not already show inline; copy/ask-again
per exchange; auto-growing composer with Enter-to-send; auto-scroll that stops
fighting a reader who has scrolled up; `aria-live` on the working indicators.

**Verified.** `pnpm build`, `pnpm lint`, `tsc --noEmit` clean; **30** vitest
tests green (16 → 30, the 14 new ones all parser). Driven end to end in
Chromium against the live api+worker: suggestion chip → stream → Stop button
present mid-stream → 8 citation chips → viewer auto-opened at `L34-108` →
trace collapse/expand → wrap toggle → mobile sheet open/close. Zero console
errors, zero horizontal overflow, and zero code overflow in wrap mode. Chat
route 181 kB → **187 kB** first load; landing unchanged at 130 kB.

**Unrelated backend bug found while testing, NOT fixed.** One run died with
`TypeError: string indices must be integers, not 'str'` raised inside
`langchain_core/utils/_merge.py:122` (`merge_lists`), reached from
`graph.py:146` `model_node`. It is a chunk-merge incompatibility between the
Mistral provider's streamed content shape and langchain_core — intermittent,
entirely within library code, and present before this frontend work. Recorded
here so it is not mistaken for a UI regression; fixing it means moving a
backend dependency pin, which is its own decision.

## 2026-07-28 — The chat route is full-bleed; the header follows the route

Reported from two screenshots: dead background either side of the chat panes.
The chat page was `mx-auto max-w-7xl` (1280px) inside a ~1347px viewport, so
~33px of unused surface sat left of the conversation and right of the code
viewer. A centred max-width is right for a document column and wrong for a
split view — **the chat route is an app shell, and app shells fill the window.**
It is now `w-full`, measured at 0px dead space on both sides at 1347px and
1920px.

**The consequence, handled rather than shipped.** Making the panes full-bleed
puts their left edge at the 16px gutter while the shared header stayed a
centred 1024px column — at 1347px the wordmark would have floated 145px right
of the pane beneath it, re-introducing the exact misalignment the landing-page
pass had just fixed. So the header's inner container is now route-aware
(`components/header-container.tsx`): full-bleed on `/repos/{id}/chat`, the
shared `.page-container` everywhere else. `layout.tsx` stays a server
component; only the container is a client boundary, for `usePathname`.

Verified: chat 0px dead space at both widths; landing and repo-status wordmark
and `h1` both at 186px — still aligned, unchanged. `pnpm build`, `pnpm lint`,
`tsc --noEmit` clean; 30 vitest tests green; no console errors.

## 2026-07-28 — Header full-bleed everywhere; hero compressed

Two requests, both about the landing page spending too much vertical space
before anything useful.

**Header.** Now full-bleed on *every* route, not just the chat one: wordmark
hard left, source link hard right, `h-12` instead of `h-14`, and a `size-6`
mark. **This supersedes the route-aware header container added earlier today**
— with every route full-bleed there is nothing to switch on, so
`components/header-container.tsx` is deleted rather than left as dead
machinery. The knock-on the earlier entry warned about is now the accepted
design: header edges no longer align with the centred content column below
them, which is the trade the request asked for. Both edges still read
symmetrically at 24px, because the source link's `-mr-2` cancels its own hover
padding.

The header shrinking by 8px moved two chat-route constants that encode it:
`h-[calc(100vh-3rem-1px)]` and the mobile sheet's `top-12`. **These three
numbers are coupled and have now been wrong twice** — worth a shared token if
the header height ever moves again.

**Hero.** Dropped the "Hybrid retrieval + symbol-graph agent" eyebrow (it
restated the stat tiles), cut the intro from 45 words to 28, `text-5xl` →
`text-3xl`, and tightened every gap plus the stat tiles. Measured at 1347px:
the URL input moved from 595px down the page to **292px**, and the repo list
from 715px to **414px** — the whole primary flow now lands above the fold on a
860px-tall viewport instead of below it.

Verified: `pnpm build`, `pnpm lint`, `tsc --noEmit` clean; 30 vitest tests
green; chat route 0 vertical and 0 horizontal overflow after the header change;
no console errors.

## 2026-07-28 — Serving hardening: the API under concurrent load

A review of `backend/app/api/` found the layering sound — thin routes, one
exception→status map, an SSE adapter that pairs tool calls by `run_id` — and
the *serving* behaviour written for one user asking one question at a time.
Seven changes, in the order they would have hurt.

**1. Connections are borrowed per tool call, not per request.** `get_conn`
checked a pooled connection out for the whole request, and the chat route's
request is an agent run: up to 8 tool calls plus N provider round-trips. With
asyncpg's unstated default of min=max=10, ten concurrent answers emptied the
pool and every other endpoint — including the ingest-progress polling the
frontend does on a timer — blocked on `pool.acquire()`.

`app/db/pool.acquire(source)` now takes **either a pool or a connection** and
yields one for the length of a block. `build_tools`, `repo_facts`, and
`answer_question` take that `ConnSource`, so the API passes the pool and the
CLIs pass the connection they already own — with no call site special-casing
the other. Pool sizing is explicit (`DB_POOL_MIN_SIZE`/`MAX_SIZE`,
`DB_COMMAND_TIMEOUT_S`), and `hybrid_search` no longer builds and destroys an
entire pool per call.

Rejected: a `Callable` connection factory. The pool-or-connection union reads
better and left the eight existing `build_graph` test call sites untouched.

**2. Torch inference moved off the event loop.** `hybrid.py` called
`get_embedder().encode(...)` and the cross-encoder's `.score(...)`
synchronously from `async def`. Each one froze the entire process for its
duration — every other answer, `/health`, the SSE heartbeats. Now
`encode_async`/`rerank_async` run them on a **bounded** `ThreadPoolExecutor`
(`INFERENCE_THREADS`, default 2), with `torch.set_num_threads` pinned so N
inference threads do not each claim every core. Singleton construction is
lock-guarded, which it had to become the moment two threads could race a cold
model.

A `ThreadPoolExecutor` rather than an `asyncio.Semaphore`: a module-level
semaphore binds to the first event loop that touches it and breaks under
pytest-asyncio's loop-per-test. The executor is loop-agnostic and queues
naturally.

**3. Time is bounded, and a disconnect stops the work.** `CHAT_TIMEOUT_S`
wraps the whole run (setup queries included — a wedged database stalls a
stream as well as a wedged provider), `AGENT_REQUEST_TIMEOUT_S` reaches every
provider client, and `ChatAnthropic`'s explicit `timeout=None` is gone. A
`CancelledError` is now counted and re-raised rather than swallowed, so a
closed tab stops the run instead of paying for an answer nobody reads. The
chat model is `lru_cache`d — it was being rebuilt per request, discarding
keep-alive to the provider and paying a TLS handshake before the first token.

**4. Limits, in Redis, without a new dependency.** Fixed-window per-IP rate
limits (`app/api/ratelimit.py`), a request-body ceiling, `max_length` on
`question` (the field that becomes billed context) and on `url`, and a cap on
concurrent ingests. `slowapi` and `prometheus-client` were the obvious
dependencies; rule 11 says ask, and both are ~30 lines against infrastructure
already present, so they are written out. Fixed window over sliding: it costs
**one Redis command per request** instead of a sorted-set read-modify-write,
and the worker's poll-delay note already records that the command budget on a
managed free tier is a real constraint. The limiter **fails open** — an
unreachable Redis already costs the ingest queue, and refusing reads because
the thing counting reads is down turns a partial outage into a total one.

**5. `GET /repos/{id}/files` is cacheable.** ETag keyed on `head_sha` + path +
range, so a repeat request is answered 304 *without reading the row*;
`Cache-Control: immutable` (the content cannot change — the commit is pinned);
optional `start_line`/`end_line`; and `GZipMiddleware`, which is safe here
because Starlette excludes `text/event-stream` by default.

**6. `/health` no longer lies.** It stays trivial — a liveness probe that fails
on someone else's outage gets this process *restarted* — and the real question
moved to `/ready`, which pings Postgres and Redis and 503s when either is
down. Startup deliberately tolerates both being unreachable, which is exactly
what made a truthful readiness endpoint necessary. Plus an `X-Request-ID` on
every response bound into every log line through a `ContextVar`, optional JSON
logs, and `/metrics` — where **`db_pool_acquire_wait_seconds` is the metric
that would have surfaced finding 1 before a user did**. Metric paths are
labelled by route template; labelling by raw path mints a series per repo id.

**7. Errors stop leaking.** `chat_stream` sent `f"{type(exc).__name__}: {exc}"`
straight to the browser and `worker.py` wrote the same into `repos.error`,
which `RepoOut` serves — an asyncpg failure carries the DSN, a provider client
can echo the key it just sent. `app/redact.py` strips credential shapes and
keeps the sentence, because replacing every message with "internal error" would
also delete the only diagnostic a failed ingest has. Unmapped exceptions get a
detail-free 500 in the same `{detail, request_id}` envelope as everything else;
previously they were a bare Starlette 500 in a different shape from the one the
frontend parses.

The catch-all lives in `RequestContextMiddleware`, not as an `Exception`
handler on the app: Starlette re-raises after running one of those, which would
leave the status metric and the access log disagreeing with what the client
actually received.

**Scope.** None of this is a Phase 6 done-when criterion. It was requested
explicitly after the review, and is recorded here as a hardening pass rather
than as phase work; Phase 6's remaining items are unchanged.

Verified: `ruff check` and `mypy app` clean; 249 backend tests green (including
the live-Postgres/Redis integration suite); frontend `tsc --noEmit` clean.

## 2026-07-29 — v2 opens as a phase sequence, not a feature flag

A review of the running system asked what it would take to serve thousands of
users. The answer is not "add a login screen". The single-user assumption is
**load-bearing in four places**, and each one fails differently:

- **Schema.** `repos.url` is globally UNIQUE (`001_init.sql:5`), so a repo is a
  singleton keyed by URL. There is no user table and no ownership edge.
- **Pipeline.** `pipeline.py:238-239` deletes and replaces a repo's content at
  the start of every ingest. Correct for one user re-ingesting their own repo;
  destructive the moment two users share a URL.
- **Worker.** `max_jobs = 1` (`worker.py:142`) with a *global*
  `count_active_ingests`, so one user's queue blocks everyone's.
- **API.** `_require_repo` (`routes.py:157`) checks that a repo **exists**,
  never who owns it, and `GET /repos` returns every row to every caller. Any
  caller holding a repo UUID can list it, chat over it, and read its files.
  That last one is a live IDOR, recorded here plainly rather than softened.

The plan is `docs/V2.md`: phases **V1–V5**, numbered to avoid collision with
v1's Phases 0–6, each with done-when criteria in the ROADMAP format. Ordering
principle: **isolate before you scale.** V1 and V2 change what the system is;
V3–V5 change how much of it there can be.

**Phase 6 closes first.** Two reasons, both load-bearing. V2's migration is
verified by running `eval.py` before and after and demanding identical numbers,
which needs a *published, frozen* baseline to compare against — closing Phase 6
is what produces one. And v1 is one human task (`demo.gif`) from being a
finished system; a half-migrated multi-tenant system demos worse than a
finished single-user one.

These six entries are written **before** any of the code exists, which is the
same discipline EVAL.md was written under in Phase 1: commit the plan while it
can still be falsified by what you find, rather than narrating it afterwards.

## 2026-07-29 — Identity is GitHub OAuth; no password authentication

v2 needs identity. It does not need a signup form. Sign-in is **GitHub OAuth,
single provider**, and the `users` table stores no credential material.

Why not email/password:

1. **Nothing to leak.** No hashes, no reset tokens, no delivery pipeline, no
   enumeration or timing surface. The parts of auth that are easy to get subtly
   wrong are absent rather than defended.
2. **It is the private-repo unlock.** ROADMAP's v2 backlog already lists
   "Private repos (GitHub tokens)". The OAuth token *is* the clone credential.
   Password auth contributes nothing toward it; this contributes all of it.
3. **The product is a GitHub tool.** "Sign in with GitHub" is the obvious
   affordance for something whose only input is a GitHub URL.
4. **Quota identity comes free** — per-IP rate limiting (2026-07-28) becomes
   per-user, which is what it should always have been.

Rejected: email/password (all of the above, and it is the least differentiated
code in the project); Clerk/Auth0 (a vendor and a bill for one provider's worth
of work); multi-provider from the start (each provider is scope, and one is
enough to prove the model).

**Dependency note — rule 11.** This needs `authlib` (or equivalent) on the
backend and Auth.js on the frontend. Both require explicit sign-off before
being added to `pyproject.toml` / `package.json`; this entry records the design
choice, not permission to install.

One thing that does **not** need to change: the chat stream can carry an
`Authorization` header because `lib/sse.ts` is a hand-rolled parser over
`fetch`, not `EventSource` (2026-07-24, 2026-07-27). A native `EventSource`
cannot set headers, and this is the step where that would have forced a
transport rewrite.

## 2026-07-29 — Corpus identity splits from the user's library: immutable snapshots

Supersedes the implicit model in `001_init.sql`, where a repo is one globally
unique row keyed by URL and a user is nobody.

```
repo_sources   (id, url UNIQUE, name)
repo_snapshots (id, source_id, commit_sha, strategy, status, …)
                UNIQUE (source_id, commit_sha)   -- immutable once ready
user_repos     (user_id, snapshot_id, added_at)  -- the per-user library
files / chunks / symbols / edges  →  snapshot_id
```

The URL-uniqueness that makes the current schema dangerous is the same property
that makes this cheap: a repo already *is* a shared object, it just has no
membership model and no immutability. Four things fall out of the split at once:

- **The delete race disappears.** Snapshots are frozen once ready; a new commit
  is a new snapshot. `clear_repo_graph` / `clear_repo_content` leave the normal
  path entirely and survive only as failed-attempt cleanup.
- **Ingest dedups.** Popular repos cluster hard. One ingest per
  `(source, commit_sha)` regardless of how many users ask for it.
- **The answer cache becomes correct**, because its key can no longer change
  underneath it. That is what makes V5 cheap.
- **Authorization is one indexed lookup**, which is the next entry.

**Ordering detail that decides whether dedup works.** The SHA is not known until
after the clone. So: shallow-clone → read HEAD → *then* look for a ready
snapshot at that SHA → short-circuit and link the user if one exists. Checking
before cloning would dedup on URL, which is wrong (two users, two commits), and
checking after ingesting would dedup nothing.

**Migration is data-preserving, and this is not optional.** It is SQL row
rewriting; **no chunk is re-embedded**. Two consequences worth writing down:
give each backfilled `repo_snapshots.id` the same UUID as its `repos.id`, so
the membership FK rewrite is a rename rather than a remap; and keep the
`repo_id` columns for one release so the whole thing is reversible.

**Verification is `eval.py`, before and after, demanding identical numbers.**
Retrieval is a pure function of the corpus. If hit@k moves, data was corrupted —
there is no benign explanation. This is the strongest regression test the
project owns and it already exists.

**One workaround retires.** `NAIVE_URL_FRAGMENT` (`pipeline.py:73-78`) mangles
a URL with `#naive` purely to dodge `repos.url` being UNIQUE, and strips it
again before cloning. Under snapshots it becomes `strategy='naive'`. A refactor
that *deletes* a workaround rather than relocating it is evidence the new model
fits; noted because the opposite outcome would have been evidence against.

Rejected: per-user copies of the corpus (multiplies 15M chunks by the user
count for zero benefit — the source is public); soft-delete on the existing
tables (keeps the race, adds a filter to every query); a `public`/`private`
flag on `repos` (does not address the race, and encodes tenancy as a boolean).

## 2026-07-29 — Authorization at the route boundary, never in the agent tools

All six tools (§7.1) take `repo_id` and scope every query by it. So a route that
has already resolved an **owned** snapshot makes everything downstream safe by
construction. Ownership is therefore checked in exactly one place —
`_require_owned_repo`, replacing `_require_repo` — and nowhere else.

Rejected: defence-in-depth checks inside each tool. It sounds strictly safer and
is not: it is six more places to get wrong, six more tests, and it would force a
user identity into the tool layer, which currently has no idea users exist. The
cost of the single check being wrong is bounded by its being *one function with
one test module pointed at it*.

**404, not 403, for someone else's repo.** A 403 confirms the UUID names a real
repo, which is precisely the fact being protected. The existing
`RepoNotFoundError` already maps to 404, so this costs nothing.

## 2026-07-29 — An ingest fleet retires the startup zombie sweep

`worker.py:117-119` justifies sweeping every in-flight row at startup: *"Startup
is the moment we know for certain no such job is running."* That is true for one
worker and **false for two** — worker 2 booting will sweep worker 1's live job
out from under it, and the symptom (a repo wedged mid-ingest) looks exactly like
the bug the sweep was written to fix.

So the fleet needs leases before it needs anything else: `claimed_by` /
`claimed_at` on the snapshot row, refreshed by a heartbeat, with the sweep
reaping **expired leases only**. This is recorded as a bug the change
*introduces*, not one that exists today — today's sweep is correct, and its
correctness is exactly what `max_jobs = 1` buys.

Two smaller choices in the same phase. Job dedup goes through a **DB unique
constraint** on in-flight snapshots per `(source_id, commit_sha)` rather than a
Redis lock: Postgres is already the source of truth for job state, and a lock in
the other datastore can drift from it. And the global `count_active_ingests`
becomes a per-user quota — as a global counter it lets one user's three queued
repos refuse everyone else's first.

`max_jobs = 1` per worker **stays**. Ingest is CPU-bound (tree-sitter, Jedi,
embedding); the fix for throughput is more processes, not more jobs per process.

## 2026-07-29 — Vector scaling stays in Postgres (reaffirms 2026-07-24)

The obvious pressure at 15M chunks is to add a dedicated vector store. Rejecting
that again, for the original reason plus a new one: hard rule 8, and the fact
that the actual problem is **index layout, not the engine**.

`chunks_hnsw` (`002_files_chunks.sql:30`) indexes `embedding` with no `repo_id`
in it, so the repo filter is applied *post-scan*. `hybrid.py:246-249` already
documents this and works around it with `SET LOCAL hnsw.ef_search = 100`. At
1,522 chunks it is invisible; across thousands of repos HNSW walks a global
graph and discards nearly everything it finds — recall collapses, or ef_search
rises and latency does. Neither is fixed by moving the same layout to Qdrant.

Two in-Postgres answers, cheapest first: `hnsw.iterative_scan = relaxed_order`
(the instance runs **pgvector 0.8.1**, where it exists and is built for this
case), then hash-partitioning `chunks` on `snapshot_id` so pruning lands a
search on one much smaller index.

*Verification caveat, recorded because it will confuse the next person:* `SHOW
hnsw.iterative_scan` errors on a freshly opened connection. That is expected —
pgvector registers its GUCs when the shared library loads, which happens on
first use of a vector operation in the session, not at connect. The extension
version (`0.8.1`, from `pg_extension`) is the reliable check.

**Sizing, measured small and extrapolated honestly.** The live instance holds
2,737 chunks in a 21 MB `chunks` table with a 5.5 MB HNSW index — ~7.7 KB and
~2 KB per chunk. At 15M chunks that is ~115 GB of table and ~30 GB of index,
and an HNSW index must be effectively resident to perform. This is arithmetic,
not a benchmark, which is why V4 sits behind a checkpoint that requires a
*measured* trigger before any of it is built.

Also deferred to that phase and gated the same way: PgBouncer (transaction mode,
`statement_cache_size=0` for asyncpg — the existing `SET LOCAL` is already
transaction-pooling-safe), and moving file blobs out of Postgres to object
storage, which the commit-keyed immutable ETag path (`routes.py:214-230`) maps
onto with no logic change.

## 2026-07-29 — Phase 1 chunk spot-check, run at last: split class chunks carry synthetic line numbers

ROADMAP's Phase 1 done-when box — *"30 randomly sampled chunks manually
spot-checked: boundaries clean, headers accurate"* — has been unticked since
Phase 1, marked "left unticked for the human review pass". It was run today,
mechanically rather than by eye: `docs/samples/phase1-sample.txt` was parsed and
every sample re-checked against the real file text in the `files` table at the
pinned `b5addb64`.

**Result: 27/30 bodies match the source exactly** (after normalising the
chunker's dedent, which is intended behaviour and not a finding). 0/30 header
`# File:` mismatches. The remaining 3 are all one root cause.

**The finding.** `chunker.py:143-144` computes a split part's line range as
`rc.start_line + s_idx` / `rc.start_line + e_idx`, where the indices are offsets
into `rc.code`. For function, method, and module chunks `rc.code` *is* the source
slice, so those offsets are real source lines and the ranges are correct.

A **class skeleton chunk is a rendering, not a slice** (SPEC §2.2: class line,
docstring, class attributes, and method *signatures* with bodies elided). Its
text has no positional relationship to the file. When §2.5 oversize splitting is
then applied to that rendering, every part after the first gets a line number
that counts rendered lines, not source lines.

Measured on `httpx._client.AsyncClient`, which splits into 18 parts:

```
part  1/18  L1307-1351   class line + docstring      — real lines, correct
part  2/18  L1352-1352   def __init__( … ) -> None: ...
part  3/18  L1353-1353   def _init_transport( … ): ...
part  4/18  L1354-1354   def _init_proxy_transport( … ): ...
…            +1 each
part 18/18  L1370-1372
```

Each elided method occupies one line in the rendering and therefore one line in
the range. The real `__init__` spans roughly 90 source lines; the real class
does not end at 1372. Source line 1359 — which part 8 claims — is
`cookies: CookieTypes | None = None,`, a parameter inside `__init__`'s signature.

**Blast radius: 69 of 1522 chunks (4.5%)** — class chunks with `part > 1`.
Everything else is correct, including part 1 of a split class (its prefix is
real source) and *unsplit* class chunks, whose range names the whole class node
and is right.

**Why it matters:** hard rule 5 — citations are not optional, and a citation is
a line range the viewer washes. A citation landing on one of these 69 chunks
highlights code the model never read. Mitigating, and the reason this has not
been visible: `get_definition` filters `c.part = 1` (`tools.py:150`), and NL
retrieval favours function/method chunks, so the defective population is rarely
the one that gets cited.

**Measured exposure, because 4.5% of the corpus is not the same as 4.5% of what
a user sees.** The 20 frozen EVAL questions were run through the production
retrieval configuration (`mode="hybrid"`, `k=SEARCH_K` — what `search_code`
uses). **12 of 200 returned chunks (6.0%) are from the defective population, and
5 of 20 questions touch at least one.** None at rank 1. Checking where each
chunk's first line of code *actually* lives in the source: 7 of 12 are rendered
stubs that appear nowhere in the file in that form, 3 are off by **+27, +48, and
+51 lines**, and 1 is off by −3. So 11 of 12 point somewhere genuinely wrong.
This is not a nominal defect.

**The fix is cheap, and an earlier draft of this entry said otherwise.** That
draft claimed the fix forces a re-ingest that moves every published number.
Wrong, and worth stating plainly because it would have driven the wrong
decision. CLAUDE.md's rule — re-ingest when the chunker changes — is right in
general and does not bind here: **both eval metrics are blind to line numbers.**
hit@k scores `file_path ∈ truth.files` OR `symbol ∈ truth.symbols`
(`eval.py:80-81`); answer-hit scores the citation's *file* (SPEC §11.2). A fix
that touches only `start_line`/`end_line` leaves chunk text, embeddings, chunk
counts, `file_path`, and `symbol` all identical — so retrieval order cannot
change and neither metric can move. That is verifiable rather than argued: run
`eval.py` after and the numbers must be identical.

The backfill needs no re-clone and no re-embed either. `files` holds the source
text, so a migration can re-derive each class node's true span with tree-sitter
from the stored content.

**Three candidate fixes.** (a) Every part of a split class reports the **whole
class span** — a few lines, provably cannot move a number, converts a silent
wrong answer into an imprecise but honest one. Loses precision: a citation to
part 8 washes the entire class. (b) Stop splitting class skeletons and emit one
over-budget chunk, the path `chunker.py:111-126` already takes for unsplittable
bodies. Simplest conceptually — splitting a summary is arguably wrong anyway —
but it **changes chunk counts and embeddings**, so it does move the corpus and
must not be done casually. (c) Track provenance in the skeleton renderer so each
part carries the true span of the methods it contains. Most correct, most work.

**Recommended: (a) now, (c) later if precision proves to matter.** Explicitly
not (b) while Phase 6's numbers are the published evidence.

## 2026-07-29 — Option (a) implemented; and the FTS leg has no deterministic tiebreaker

Option (a) is in. `chunker.py` defines `RENDERED_KINDS = {"class", "module"}` —
kinds whose text is assembled rather than sliced — and every part of such a
chunk now reports the whole node span instead of an offset into its own
rendering. Module chunks are included because they gather docstring, imports,
and top-level assignments while stepping over every def and class between them;
measurement found 6 of 19 split module chunks were also wrong, which the
original entry had not caught.

`005_rendered_chunk_spans.sql` backfills existing rows from the `symbols` table,
which already stores the true tree-sitter span. No re-clone, no re-embed. Join
coverage was verified first: **85/85 class and 19/19 module** split chunks
resolve to a symbol. After migration, **104/104 carry a span identical to their
symbol's**, and `httpx._client.AsyncClient` reports 1307-2019 on all 18 parts
instead of 1307-1351, 1352, 1353, 1354…

**Verification: every hit@k held; one MRR moved; and the prediction was too
strong.** The previous entry claimed "neither metric can move". Nine hit@k
values across three modes are byte-identical to the 2026-07-27 baseline, and the
per-question hit@10 grid matches question for question. But **fts MRR moved
0.503 → 0.494**, so the claim as written was wrong and is corrected here.

The cause is not the fix, which cannot touch ranking — it changes no chunk text,
no embedding, no `symbol`, no `file_path`. It is that `_fts_leg` orders by
`ts_rank(tsv, q) DESC` with **no tiebreaker** (`hybrid.py:226`), and measurement
shows **19 of 20 questions have tied ts_rank scores inside the FTS top-10, 64
tied slots in total**. Tied rows come back in physical heap order. A Postgres
`UPDATE` writes new row versions at new locations, so rewriting 104 rows
reshuffled ties, which moves a rank-sensitive metric while leaving every
threshold metric untouched. That is exactly the observed signature: fts hit@3,
hit@5 and hit@10 all identical, fts MRR moved, hybrid MRR unchanged at 0.752
because RRF is dominated by the vector leg. Repeated identical queries return
identical order, so this is not run-to-run nondeterminism — it is a persistent
function of physical row layout.

**The real finding is the latent one: published FTS MRR figures are not
reproducible across physical row reorganisation.** Any UPDATE, VACUUM FULL,
re-ingest, or restore would move them by a comparable amount, and always has —
this change merely triggered it visibly for the first time. hit@k is immune,
which is why the headline numbers have been stable all along.

The one-line fix is `ORDER BY ts_rank(tsv, q) DESC, id` in both `_fts_leg` and
the fusion CTE. Deliberately **not** applied here: it would itself shift FTS MRR
once more, to a new and finally stable value, and that is a change to published
evidence that should be made on purpose and re-measured, not smuggled in
alongside a citation fix. Logged as the next candidate.

Verified: 248 backend tests green (2 new chunker tests — split class parts report
the whole class span; split *function* parts keep true source offsets, so the fix
cannot silently widen to contiguous kinds), `ruff check` clean, `mypy app` clean
across 42 files, corpus still 825 impl / 697 test.

**Environment note.** `scripts/eval.py` dies with `UnicodeEncodeError` on a
default Windows console: it prints `✓` in the per-question grid and cp1252 cannot
encode it. It crashes *before* the EVAL.md append, so no partial block is
written. `PYTHONIOENCODING=utf-8` is the workaround. Worth a README line, since
"a stranger can run it locally" is a Phase 6 criterion and this is a stranger on
Windows hitting it at the last step.

**The box stays unticked, and that is the honest outcome** — the criterion says
"boundaries clean", and for 4.5% of chunks they are not. Its note has been
updated from "pending a human" to what the run actually found. A criterion that
sat unchecked for five phases and then caught a real defect on first execution
is an argument for the criterion, not against it.

**Two minor observations, neither a defect.** `phase1-sample.txt` is written in
the Windows default codepage, not UTF-8 (`clé` in an httpx test string), so it
raises `UnicodeDecodeError` on a UTF-8 host — it is a docs artifact, and the
database copy is correct. And the skeleton renderer double-spaces class
docstrings (a blank line after every line), which costs tokens and looks odd in
agent context but changes nothing about correctness.

## 2026-07-29 — Every retrieval ordering carries `id`; the numbers were re-measured once, on purpose

The previous entry logged the FTS leg's missing tiebreaker as the next
candidate and deliberately left it. Doing it now, and the first thing
measurement changed was the scope: **ties are not an FTS problem, they are a
retrieval-wide one.** Across the 20 frozen questions, at each leg's top-40:

| leg | questions with ties | tied slots |
|---|---|---|
| FTS | 20/20 | 489 |
| vector | 11/20 | 59 |
| RRF fusion | 20/20 | 236 |

Fusion ties are structural rather than incidental: a chunk found by one leg
only scores `1/(RRF_K + rank)`, so a chunk at rank 5 in the vector leg and a
*different* chunk at rank 5 in the FTS leg collide exactly. Fixing FTS alone
would have left 236 arbitrary slots and looked like a fix.

So `id` is now the tiebreaker in **every** ordering in `hybrid.py`: both legs,
both fusion CTEs — in the `ROW_NUMBER` window *and* the matching `ORDER BY`, or
the rank assigned would disagree with which rows survive the `LIMIT` — and the
final `ORDER BY rrf DESC, chunk_id`. `_inject_symbol_ids` also had a bare
`LIMIT` with no `ORDER BY` at all, which is the same bug with no ordering to
tiebreak; it now orders by `id` too. The injection path is dormant with rerank
off, so that one was latent rather than active.

**Plan-neutral.** `EXPLAIN` before and after is byte-identical for the vector
leg, with and without `enable_seqscan`. Worth recording *why*: at this corpus
size the planner picks `chunks_repo_file` and sorts, and **never chooses
`chunks_hnsw` at all** — the `repo_id` predicate is more selective than the
vector ordering is valuable. That is another data point for the V4 gate: the
HNSW index's behaviour under filtering is effectively untested here, and adding
a secondary sort key to a query that *does* use it would need re-checking then.

**The re-measurement, which is the point.** This change moves published numbers
by construction — that is what fixing a tie order does — so it was done as one
deliberate experiment, four eval blocks, framed in EVAL.md:

| | pre-fix | post-fix |
|---|---|---|
| vector | 0.75 / 0.85 / 0.90, MRR 0.722 | **unchanged** |
| fts | 0.60 / 0.70 / 0.80, MRR 0.494 | 0.55 / 0.70 / 0.80, MRR 0.463 |
| hybrid | 0.80 / 0.85 / 0.95, MRR 0.752 | 0.80 / **0.90** / 0.95, MRR 0.753 |

**The new values land on the 2026-07-26 published numbers** — fts hit@3 0.55,
hybrid hit@5 0.90 — which is the strongest evidence that this is a correction
rather than a drift. That corpus had just been ingested, so physical heap order
still *was* id order; every write since (the naive baseline on 07-27, the span
migration on 07-29) walked it away. The tiebreaker pins permanently what was
briefly true by accident.

**Proof, not assertion.** Two consecutive runs are byte-identical. A third,
taken after `UPDATE chunks SET part = part` rewrote all 1522 row versions — the
exact mechanism that shifted fts MRR by 0.031 when the span migration rewrote
only 104 — is also byte-identical. 14× the perturbation, zero movement.
`test_retrieval_order_survives_a_row_rewrite` pins it: capture the id sequence
for all three modes, rewrite every row, assert the sequence is unchanged.

**Note for whoever runs the integration suite.** It cannot complete on this
Windows host: `test_db_ingest_idempotent_and_search` uses `mode="hybrid+rerank"`,
and loading the 2.4 GB `bge-reranker-v2-m3` CrossEncoder kills the interpreter
with a `Windows fatal exception: access violation` inside torch's weight
materialisation. Nothing to do with this change — the new test avoids the
reranker and passes in 78 s. Rerank is off by default (SPEC §5.3), so this only
bites the integration suite.

## 2026-07-29 — V1 built: opaque session tokens, one enforcement point, and a cookie that will not survive a two-domain deploy

SPEC §13 written first, per V2.md's own prerequisite rule, then implemented.
Three choices are worth more than the diff.

**The session token is not a JWT.** A JWT's value is that a third party can
verify it without asking the issuer. Here the only verifier *is* the issuer and
the only claim is a user id, so what is left of the format is a header nobody
reads and an `alg` field that has been its own vulnerability class. Instead:
`v1.<user_id>.<expiry>.<hmac-sha256>`, base64url, compared with
`compare_digest`, signature checked *before* the expiry is read so an unsigned
expiry is never trusted. The version prefix is inside the signed material, so a
future format change cannot be replayed against the old parser.

**Authorization is enforced in exactly one function.** `_require_repo` became
`_require_owned_repo`, and all five `/repos` routes call it. The six agent tools
(§7.1) already scope every query by `repo_id`, so a route that resolved an
*owned* repo makes everything downstream safe by construction. Rejected:
defence-in-depth checks inside each tool — six more places to get wrong, six
more tests, and a user identity forced into a layer that has no other reason to
know users exist. The blast radius of the single check being wrong is bounded by
its being one function with a test module aimed at it.

An unowned repo answers **404, not 403**, and ownership is checked **before**
readiness — otherwise a stranger's indexing repo would answer 409 "repo not
ready" and confirm both that it exists and what it is doing.

**Rate limits re-key to the user, and the token is verified before it is
believed.** Parsing the subject without checking the signature would let anyone
mint a fresh quota by editing a cookie. A forged or junk token falls back to the
IP rather than raising: this middleware counts, it does not authenticate, and an
exception on attacker-controlled cookie input is a 500 anyone can trigger from a
browser console.

**Zero new dependencies.** `httpx` moved dev → main; the OAuth dance is ~130
hand-written lines, following the 2026-07-28 precedent that set `slowapi` and
`prometheus-client` aside for the same reason. The GitHub access token is used
once and never stored — `read:user` grants nothing else, and keeping it would
mean holding a live credential per user in exchange for no feature.

**The deployment trap, recorded because it fails silently.** `SameSite` is
evaluated against the registrable domain, not the origin — ports are ignored. So
`localhost:3000 → localhost:8000` is *same-site* and `Lax` cookies flow in
development. `app.vercel.app → api.fly.dev` is **cross-site**: the browser sends
no cookie, every request arrives anonymous and 401s, and neither log says why.
Three ways out are in SPEC §13.4 in preference order; the frontend already sends
`credentials: "include"` everywhere, including the SSE stream, which is
necessary for all of them. This is also the payoff for the 2026-07-27 decision
to parse SSE over `fetch` rather than use `EventSource`, which can neither send
credentials cross-origin nor set a header.

**Migration `006_users.sql` is additive** — no row rewritten, deliberately,
since 2026-07-29 is a standing reminder of what rewriting rows does to tied
orderings. Every pre-auth repo is adopted by a placeholder user (`github_id 0`,
a value no real account can hold) which the first sign-in matching
`BOOTSTRAP_GITHUB_ID` takes over, library included. Verified on the live
database: 12 of 12 repos adopted, 0 orphans, corpus still 825 impl / 697 test.

**Frontend gating is client-side, not Next middleware.** Middleware can only
read cookies on the frontend's own origin; on localhost that happens to be the
same site, so it would appear to work and then fail the first time the API is
deployed separately. Asking `/auth/me` behaves identically in both. The backend
is the real enforcement either way — this only decides what renders.

Verified: 286 backend tests (24 tenancy, 14 token), `ruff` and `mypy` clean
across 46 files; frontend `pnpm build`, `pnpm lint`, `tsc --noEmit` and 30
vitest tests clean. **One V1 done-when box stays open**: the live browser
sign-in, which needs a registered OAuth app's credentials.

## 2026-07-29 — V2 schema landed: `strategy` belongs in the snapshot key, and a ready snapshot is frozen

SPEC §14 written first, then `007_snapshots.sql`. Schema only — no code reads
`snapshot_id` yet, and `repo_id` is still populated beside it, so the app runs
unchanged and the migration is revertible without a restore (§14.8).

**The plan's unique key was wrong, and the data said so.** V2.md specified
`UNIQUE (source_id, commit_sha)`. httpx's AST and naive corpora sit at the
**same commit** `b5addb64` and are kept apart today only by the `#naive` URL
fragment this phase exists to retire — so a two-column key rejects the second
corpus outright. It is `UNIQUE (source_id, commit_sha, strategy)`. Worth
recording as a plan error rather than quietly fixing it: the fragment hack was
load-bearing in a way the plan had assumed it was not.

`commit_sha` stays nullable. It is unknown until the clone reports it, and
Postgres treats NULLs as distinct in a unique index, so several queued attempts
on one source coexist. That is correct — they are separate attempts — and real
in-flight dedup is V3's lease work, not a constraint here.

**Verification is stronger than the criterion asked for.** The done-when wanted
a spot-checked embedding; instead, sha256 over `id:embedding::text` for *all
1522* httpx chunks, ordered by id, is byte-identical across the migration —
`17fb8fc8ad9f6213ccec7b507ec5fa7c734403b104457a9a0f58bdad6b4a7551` — and 0 rows
across `files`, `chunks`, `symbols`, `edges` and `user_repos` have `snapshot_id`
differing from `repo_id`. 12 repos became 11 sources and 12 snapshots, the two
httpx rows collapsing onto one source exactly as §14.2 intends.

Snapshot ids **are** the old repo ids, deliberately: the `user_repos` rewrite is
then a rename rather than a remap, and every repo id already handed to a browser
still resolves.

All six access paths that existed on `repo_id` are mirrored onto `snapshot_id`.
Adding the column without them would make the first query through it a
sequential scan of the whole corpus — the kind of regression that shows up as
"the app got slow after the migration" rather than as a failure.

**Flagged before it is built, not after: §14.5 changes observable behaviour.**
Re-submitting a URL whose snapshot is `ready` will return that snapshot instead
of re-ingesting it. That is the destructive path this phase removes, so it is
the intended fix — but it is a real change to what `POST /repos` does, and the
frontend's Retry button survives only because Retry acts on a **failed**
snapshot, which still creates a new one.

Nothing else in the code layer has moved yet: `queries.py` still takes
`repo_id`, `pipeline.py` still calls `clear_repo_*` at ingest start, the worker
has no post-clone SHA dedup, and `NAIVE_URL_FRAGMENT` is still there. The
eval-equality check that the phase lives or dies on only becomes meaningful once
retrieval actually reads `snapshot_id`.

Verified: 289 tests green, unchanged, because the old column still answers.

## 2026-07-30 — V2 code layer: the race is closed, and a read-only verification nearly hid a write-fatal bug

The schema landed on 2026-07-29; this is the half that uses it. `queries.py`,
`hybrid.py`, `tools.py`, `pipeline.py`, `routes.py`, the worker and the CLIs all
address `snapshot_id` now, and `POST /repos` follows §14.5.

**The phase's criterion passed exactly.** `scripts/eval.py`, run through the new
snapshot code path, reproduces all twelve baseline metrics unchanged: vector
0.75/0.85/0.90 MRR 0.722, fts 0.55/0.70/0.80 MRR 0.463, hybrid 0.80/0.90/0.95
MRR 0.753. Retrieval is a pure function of the corpus, so this is the strong
form of "the data survived the rewrite".

**A bug that reads perfectly and writes not at all.** 007 kept the legacy
`repo_id` columns for reversibility (§14.8) but left their NOT NULL in place,
and the new code writes only `snapshot_id`. Every existing row therefore read
back correctly — the entire eval suite passed, twice — while **every new ingest
failed** on a not-null violation. `008` drops those constraints.

Worth recording as a lesson rather than a fix: a verification built entirely
from reads cannot see a write-path break. The only check that caught it was the
new integration test, because it is the only one that performs a real ingest
instead of querying the corpus the migration produced. `008` also had to move
`user_repos`'s primary key off `repo_id` — a PK column cannot be nullable — onto
the `(user_id, snapshot_id)` unique index 007 had already created, so uniqueness
is guaranteed continuously with no window for a duplicate library row.

**Dedup demonstrated by a test I wrote wrong.** The interleaving test first
failed with `SnapshotSuperseded`: it created a second snapshot of the same
source at the *same commit*, and §14.4 correctly refused to build a duplicate
corpus. The dedup was right and the test was wrong. Fixed by making a genuine
second commit — which is what the test should have done, since two corpora can
only be independent if they are of different things. It now captures the exact
chunk ids the first snapshot serves, runs a full second ingest, and requires the
identical sequence afterwards. That is the race §14.1 exists to close, asserted
against a live database rather than argued.

**`SnapshotSuperseded` is an exception for a success.** Raised once the clone
reveals the commit is already ingested, after the redundant snapshot's library
rows have been moved and its row deleted. A return value would have to be
threaded through every frame between the clone context and the worker; the
worker and the CLI both treat it as a successful outcome and neither writes a
`failed` status.

**A clone-cleanup failure no longer destroys a finished ingest.** `_rmtree` runs
in the `finally` of `cloned_repo`, so the `PermissionError` Windows raises when
a scanner holds a pack file replaced the block's result — turning a completed
ingest into a `failed` snapshot for a corpus already safely in Postgres. It is
best-effort now and logs a warning. Found while chasing the test above; the
clone is scratch and the database is the durable copy (2026-07-24).

**Two deliberate, observable changes.** Re-submitting a URL whose snapshot is
`ready` returns it and enqueues nothing — the destructive re-ingest is gone, and
Retry still works because Retry acts on a `failed` snapshot, which is superseded
by a *new* row rather than reset. And the metrics route template is now
`/repos/{snapshot_id}`, renaming one Prometheus series; the new name is the
accurate one, but anything graphing the old label would need updating.

**`#naive` is gone from code and data.** 0 sources with the URL fragment, 0
names with the `@naive` suffix; both httpx corpora sit under one source at
`b5addb64`, told apart by `strategy` (ast 1522 / naive 657). The workaround was
deleted rather than relocated, which is the outcome §14.6 predicted.

Verified: 294 tests green (5 new), `ruff` and `mypy` clean across 46 files,
corpus still 825 impl / 697 test. One V2 box is left open on purpose: the
rollback has not been rehearsed, and it is now lossy for anything ingested after
`008`, so it should be rehearsed before it is relied on.

## 2026-07-30 — V3 leases: the plan's premise was wrong, and the first claim was vacuous

SPEC §15/§16 written first, then `009_job_leases.sql` and the lease half of the
code. Two corrections matter more than the feature.

**The justification V2.md gave for this phase was false.** It claimed worker 2's
startup would sweep worker 1's live job, calling leases "the precondition for a
second worker existing". `sweep_zombie_repos` has always been *time-based*
(`updated_at < now() - ZOMBIE_AFTER_S`), never startup-scoped, and `job_timeout`
(900s) sits deliberately below `ZOMBIE_AFTER_S` (1200s) so ARQ cancels a wedged
job before the sweep can reach it. **A second worker was already safe.** Written
up in §15.1 rather than quietly fixed, because a plan that overstates a risk
earns the same distrust as one that misses it.

The real hole is narrower: **progress writes are incidental, not a heartbeat.**
`linking` sets its status once and then runs Jedi silently to completion, so only
the 900s job timeout bounds how long a healthy job can look stale. Raise
`JOB_TIMEOUT_S` past 1200 without a heartbeat and the sweep starts killing live
work. A heartbeat is unconditional, which is what lets `LEASE_EXPIRY_S` be 120s
instead of 1200s — a dead worker's job returns in two minutes rather than twenty.

**The first version of `claim_snapshot` did nothing.** It guarded on
`status = 'queued'` but did not *change* the status, so two workers both matched
the UPDATE, both believed they held the lease, and both would have ingested into
one snapshot — doubling every row. The status transition is now part of the claim
statement. Caught by `test_only_one_worker_can_claim_a_snapshot`, which is a live
database test precisely because a fake connection routing SQL by substring would
have reported the statement was sent and told us nothing about whether it was
exclusive.

**Dedup is keyed on `(source_id, strategy)`, not `(source_id, commit_sha)`** as
V2.md specified — a second plan error. `commit_sha` is NULL until the clone
reports it and NULLs are distinct in a unique index, so a commit-based constraint
would admit unlimited queued duplicates: exactly the work it exists to prevent.
The index is *partial* (in-flight statuses only) so a source still accumulates
`ready` snapshots over time, which is the §14 model.

**Two sweeps, disjoint by construction.** The lease sweep reaps rows with a stale
`heartbeat_at`; the old zombie sweep reaps rows with **no** `heartbeat_at` at
all, which is every snapshot predating these columns. Neither can reap what the
other owns, so the two windows do not fight.

`touch_heartbeat` deliberately leaves `updated_at` alone: that column means "the
job made progress", and conflating it with "the worker is alive" is the exact
confusion that left the old sweep unable to tell a silent `linking` phase from a
dead process.

**Per-user quota (§15.5).** `count_active_ingests` was global, so one user's
three queued repos refused everybody else's first submission — a per-user limit
dressed up as capacity protection. Real capacity is bounded by the worker fleet
and by the one-in-flight index.

Verified: 294 unit tests, 12 integration (7 new lease tests against the live
database), `ruff` and `mypy` clean. **The inference service half of V3 (§16) is
not built** — the API still imports torch.

## 2026-07-30 — V3 inference service: the API is off torch, and the saving was not where §16 said

`app/inference/` is the §16 service; `HttpEmbedder` is the client behind the
existing `Embedder` Protocol, selected by `INFERENCE_URL`. Unset keeps the local
model, so development, the CLIs and a single-box deployment are unchanged — the
remote path is opt-in.

**The measurement moved the work.** §16.1 justified this by the embedding model:
130 MB per replica, 2.4 GB with the reranker. Measured working set on this host:

| | RSS | modules |
|---|---|---|
| bare python | 11 MB | 64 |
| `import torch` | 185 MB | 1124 |
| `import app.main` (before) | 264 MB | 2641 |
| + embedding model loaded | 444 MB | 4584 |

So the model costs **180 MB** — and **torch itself costs 185 MB**, slightly
*more* than the model it was supposed to be dwarfed by. Removing only the model
would have left the larger half in place.

**Where torch came from, and it was not the embedder.** Bisecting the import
graph: `app.ingest.embedder` and `app.retrieval.hybrid` are both clean — rule 3's
deferral into the constructors works exactly as documented. The culprit was
`langchain_core.language_models.chat_models`, imported in four modules **purely
for the `BaseChatModel` type**, which pulls `transformers` and therefore torch.

Three of the four moved under `TYPE_CHECKING` with no cost at all. The fourth,
`deps.py`, is the interesting one: `ChatModel = Annotated[BaseChatModel,
Depends(...)]` is evaluated at import time, so a real class there drags torch in,
and a string forward reference does not work either — FastAPI resolves it with
`get_type_hints` against module globals, where a `TYPE_CHECKING` import is
absent. It is `Annotated[Any, Depends(get_chat_model)]` now, with the reasoning
in a comment. What is lost is the annotation on route parameters; `get_chat_model`
itself stays fully typed, so nothing downstream reasons about a weaker type.

**Result: `import app.main` is 264 MB → 83 MB, 2641 → 1319 modules.** With
`INFERENCE_URL` set the model never loads either, so an API replica goes
**444 MB → 83 MB** — a 5.3× reduction, and more than half of it from a type
annotation nobody would have suspected.

**The test runs in a subprocess, and has to.** By the time pytest reaches it,
earlier tests have loaded torch into `sys.modules`; an in-process assertion would
pass no matter what the API imported. It probes `app.main`, `app.retrieval.hybrid`
and `app.ingest.embedder` separately, so a regression names the layer.

**Client contract, unit-tested against a stub rather than a model.** Order
(`vectors[i]` ↔ `texts[i]`), splitting a 600-text batch across the service's
512-text ceiling and reassembling *in order*, and reading `dim` over the wire at
construction — trusting configured `dim` would let a service on a different
`EMBEDDING_MODEL` write vectors from another space into the corpus, which no
dimension check catches and no retrieval metric explains.

`token_len` is one round trip per call and the chunker calls it per oversize
check (~1400 times on httpx). Kept rather than approximated — a heuristic would
silently move chunk boundaries, and chunk boundaries are the corpus — but ingest
workers should leave `INFERENCE_URL` unset and run the local model. Recorded in
§16.3 rather than left to be discovered as a slow ingest.

Verified: 304 unit tests, `ruff` and `mypy` clean across 48 files, and
`scripts/eval.py` unchanged (0.75/0.85/0.90 · 0.55/0.70/0.80 · 0.80/0.90/0.95).

**Not done:** eval has not been re-measured *through* a running service. The
client is stubbed and the local path is unchanged, but the end-to-end equality
§16.5 asks for needs the service actually stood up.

## 2026-07-30 — The three-worker run found a bug that bricks a commit forever

V3's two multi-worker criteria say "verified by running it, not by reading the
code". Ran both. The first passed cleanly; the second failed, and what it found
justifies the whole insistence on execution over inspection.

**Three workers, three repos, concurrent.** Three real ARQ processes against
three distinct sources: all three `ready`, 24 chunks each, three *distinct*
`claimed_by` values. Jobs are enqueued **before** the fleet starts so all three
are waiting when it comes up — enqueue afterwards and one warm worker finishes a
small repo before the others poll, which proves nothing about concurrency.

**Kill one worker: three of four properties held immediately.** `taskkill /F` on
the worker holding a mid-`parsing` snapshot. Survivors reached `ready` 2/2 — the
fleet is not coupled. An immediate sweep spared the orphan because its lease was
still fresh (`swept=0`), and a sweep after `LEASE_EXPIRY_S` reclaimed exactly it.

**The fourth failed: a retry was impossible.** Every attempt died with

    UniqueViolationError: duplicate key value violates unique constraint
    "repo_snapshots_source_id_commit_sha_strategy_key"
    Key (source_id, commit_sha, strategy)=(…, 87072f6d…, ast) already exists.

007 made `(source_id, commit_sha, strategy)` **unconditionally** unique. A
snapshot that dies after cloning keeps its `commit_sha`, and the lease sweep
marks it `failed` without clearing it — so the corpse owns that commit. The
retry clones the same repo, gets the same sha, and collides. **One worker death
made that commit permanently un-ingestable**, for that repo, forever, and the
error surfaced as a raw Postgres message that named a constraint rather than a
cause.

Nothing about this is visible by reading either piece. The constraint looks
correct in 007. The sweep looks correct in 009. The interaction is only reachable
by killing a process mid-ingest and then trying again.

**`010` narrows the index to `status = 'ready'`.** The constraint's purpose is
"one stored corpus per repo, commit and strategy" (§14.2); a failed snapshot is
not a corpus — it is a partial write nobody can read, because a non-`ready`
snapshot is not servable. Safe against two attempts racing to `ready`, because
009's one-in-flight index already permits a single in-flight snapshot per
`(source_id, strategy)` and §14.4 short-circuits a second attempt at a stored
commit before it ingests anything.

Two regression tests pin both halves: a `failed` snapshot no longer blocks a
retry at its commit, **and** two `ready` snapshots of one commit are still
refused — narrowing an index is only safe if the narrowed population stays
exclusive.

**A pattern worth naming, because it is now three for three.** Every bug this
session came from an interaction that read correctly in isolation: the span
migration exposed the missing tie ordering; the `NOT NULL` on legacy columns
passed every read and broke every write; this one needed a process kill plus a
retry. All three were found by execution — a mechanical spot-check, an eval
re-run, a real kill — and none by inspecting the diff.

Verified: 304 unit tests, 14 integration (9 lease tests), `ruff` and `mypy`
clean, `eval.py` hybrid unchanged at 0.80 / 0.90 / 0.95 MRR 0.753.

## 2026-07-30 — Eval through the live inference service: byte-identical vectors

§16.5's criterion is that `scripts/eval.py` be unchanged with the HTTP embedder
in place — same corpus, same vectors, different transport. Run against a live
service:

    uv run uvicorn app.inference.main:service --port 8001
    INFERENCE_URL=http://localhost:8001 uv run python scripts/eval.py --mode vector,fts,hybrid

All twelve metrics identical to the local path: vector 0.75/0.85/0.90 MRR 0.722,
fts 0.55/0.70/0.80 MRR 0.463, hybrid 0.80/0.90/0.95 MRR 0.753. `/health`
confirmed the service was on `BAAI/bge-small-en-v1.5` at dim 384, and
`get_embedder()` returned `HttpEmbedder` rather than the local model — worth
checking explicitly, because an eval that silently fell back to the in-process
model would produce exactly the same numbers and prove nothing.

**Then the stronger check, because equal rankings are not equal vectors.** Local
and remote embeddings of the same four texts — including a 400-character one —
compared directly: **byte-identical, 4/4, maximum absolute difference exactly
0.0**, and `token_len` agreeing on all four. A float discrepancy small enough not
to reorder any result would have passed the eval criterion and failed this one,
so "same vectors" is literal rather than inferred.

A small corroboration noticed in passing: the first three components the service
returned for *"how does the client pick a transport"* are
`-0.028223, -0.03184, 0.007634` — the same values a locally computed query vector
for that string showed earlier in the session, in an unrelated `EXPLAIN` probe.

**V3 is complete, 8/8.** The fleet is verified by running it (three concurrent
workers, and a killed worker that exposed the commit-bricking unique key), and
the inference service is verified by measuring through it.

fts is included in the run deliberately even though it needs no embedder: it is
the control. If the harness had been misconfigured in some way that changed the
corpus rather than the transport, fts would have moved too.


## 2026-07-31 — Tier 0 built: the graph answers questions without the agent

**The choice.** Two new capabilities — a module dependency rollup and test↔code
linkage — ship as **HTTP endpoints, not agent tools** (SPEC §18). Plus four
frontend/CLI affordances that add no backend surface at all: a theme toggle, an
"Explain" action in the code viewer, `?q=` prefill on the chat route, Markdown
export of a conversation, and `--json` on the ingest CLI.

**Why endpoints.** `AGENT_TOOL_CAP` is 8, and Phase 5's live run *hit* it —
`search_code ×4, expand_context, read_file ×3`. FEATURE-IDEAS treats "adds a
seventh tool" as a bookkeeping step needing a DECISIONS entry. It is not: with
the budget unchanged, every added tool changes how the existing eight executions
get spent, which is an unmeasured risk to answer quality on the one thing this
project claims to be good at.

Both of these questions have exact answers in SQL. Routing them through the
model would spend a scarce budget to compute what a `GROUP BY` already knows,
and would make a deterministic result non-reproducible. The rule this sets, and
the reason it is worth writing down: **if the symbol graph can answer it
exactly, it is a query; the agent is for what needs judgement.**

**Why this was cheap.** Nothing new is extracted and no migration was needed.
`symbols` already carries `file_path`/`is_test`, `edges` already carries
`kind`/`snapshot_id`, and in Python a file *is* a module — so `symbols.file_path`
is the module key directly, with no string surgery that could disagree with the
graph it summarises. The whole of §18 is four SQL statements against tables that
have existed since `004`.

**Three deliberate calls inside it.**

1. *Same-file edges are excluded from the rollup.* A module calling itself says
   nothing about architecture and would dominate fan-in on any large file.
2. *An unknown `path` on `/coverage` returns empty lists, not 404.* "Not
   indexed" and "no test reaches it" are the same answer to the question asked,
   and separating them turns the endpoint into an existence oracle for paths —
   §13.5's reasoning one level down.
3. *`covers` is empty for an implementation file.* Reporting its outgoing call
   edges there would be a different question wearing this one's name.

**`--json` is a stdout contract, not a flag.** The ingest CLI's progress lines
went to stdout via a hardcoded `print`, so a JSON mode that only changed the
final block would still have emitted an unparseable document. `run_ingest`'s
`log` is now a parameter; in `--json` mode every human-readable line — progress,
the no-owner warning, `--sample` output — goes to stderr, stdout carries exactly
one object carrying `ok` on success *and* failure, and the exit status mirrors
it. Both CLIs behave the same way.

**Eval was not run, on purpose.** Nothing here touches `app/ingest/` chunking,
`app/retrieval/`, the embedder, or the agent loop; the retrieval path is
byte-identical. Re-running `scripts/eval.py` would measure the same corpus
through the same code and prove nothing. The rule stands for anything that
*does* touch ingest or the tool set — which is exactly why these six were built
first.

**Scope note.** This is v1/v2 feature work taken deliberately while V2.md has
V4/V5 unstarted and one open box in V2. It touches no ingest path and no
retrieval, so it cannot invalidate the eval-equality verification those phases
rest on. Anything from FEATURE-IDEAS that *does* touch them stays behind those
open boxes.

---

## 2026-07-31 — Three integration tests were never migrated to snapshots

Found while running the full suite for the entry above, then repaired.
`tests/worker/test_worker_integration.py` (×2) and
`tests/retrieval/test_integration_db.py::test_db_ingest_idempotent_and_search`
were written against the pre-V2 schema and stopped being run at some point
after V2 landed. **V2.md's V3 entry claims "294 unit + 12 integration" clean;
that had not been true since `007`.**

Not a rename. Four separate things had rotted, and only the first was loud:

1. `queries.create_repo` no longer exists — V2 split it into
   `get_or_create_source` + `create_snapshot`, because a source is created once
   per URL and a snapshot once per ingest attempt. `AttributeError`, so these
   two failed immediately.
2. `SELECT count(*) FROM symbols WHERE repo_id = $1` — `008` made the retained
   legacy `repo_id` nullable and new rows write only `snapshot_id`, so
   `assert n_symbols > 0` could no longer pass on a fresh ingest.
3. **The teardown had been silently deleting nothing.** `DELETE FROM repos WHERE
   url = $1` matches no row for a post-V2 ingest, so every run of these files
   leaked a source, a snapshot, and its entire corpus into the live database.
   This affected the *passing* tests too, which is why nothing surfaced it.
   Teardown is now `DELETE FROM repo_sources`, which cascades.
4. The idempotency assertion had quietly lost its meaning **twice over**:
   - §14.4 dedup replaced delete-and-replace, so a second `ingest_to_db` of the
     same commit now raises `SnapshotSuperseded` rather than rebuilding. The
     invariant under test is unchanged — one commit, one stored corpus — so the
     test now asserts the dedup path and, additionally, that nothing was rebuilt.
   - §15.4's lease means a second *job* on a `ready` snapshot is never claimed.
     The old test ran the worker twice and asserted the counts had not changed —
     which, post-lease, passes because **the second run does nothing at all**.
     "Nothing changed" is also what a silently broken queue looks like. The test
     now asserts the refusal directly against `claim_snapshot`, then resets the
     row to `queued` for a genuine retry and asserts delete-and-replace there.

Also added `test_the_two_sweeps_do_not_reap_each_other_s_rows`. Both sweeps run
back to back on every worker startup over the same statuses; what keeps them
apart is one predicate each (`heartbeat_at IS NOT NULL` vs. the much longer
timer). §15.4 asserts it and nothing tested it, and getting it wrong means one
worker failing a snapshot another is actively ingesting.

**One flake, diagnosed rather than retried.** The first run of the rewritten
worker test died on a Redis connection timeout inside ARQ's own pipeline. Cause
was mine: the restructure had taken worker spin-ups from two to three, and the
Redis Cloud free tier's command/connection budget is already a documented
constraint (`app/worker.py`, `POLL_DELAY_S`). Fixed by dropping back to two —
the "already ready is not claimable" case does not need a worker at all, since
it is a property of `claim_snapshot`. Fewer connections *and* a more precise
assertion; a retry loop would have bought neither.

**Verified:** 6 integration tests green against live Postgres + Redis
(`3 passed` worker, `3 passed` retrieval), ruff and mypy clean.

---

## 2026-07-31 — Load checkpoint: NO-GO on V4, and the latency number nearly said otherwise

V2.md requires the ruling and the measured numbers be recorded either way. Both
are below. **Ruling: NO-GO.** V4 is not started and should not be.

**Corpus, measured on the live instance** (after the test-debris cleanup below):

```
chunks           2,725          snapshots  8      sources  7      users  2
chunks table     24 MB (incl. indexes)  ->  8.8 kB/chunk
HNSW index       5.5 MB                 ->  2.1 kB/chunk
pgvector         0.8.1
```

V2.md's own extrapolation note was written at **2,737 chunks / 21 MB**. The
corpus has since moved by **-12 chunks**. Its stated bar — *"At 100K chunks the
work in V4 buys nothing"* — sits **37× above** where the corpus actually is.

**The latency measurement is the interesting half, because read carelessly it
fires a false GO.** End-to-end hybrid search, 30 calls over the httpx corpus:

```
p50 1,776 ms      p95 3,090 ms
```

Three seconds at p95 looks exactly like the index problem V4 exists to solve.
It is not. Decomposed:

```
network RTT (SELECT 1)    p50   252 ms   p95   477 ms
query embedding (CPU)     p50    22 ms   p95    79 ms
RRF fusion statement      p50   250 ms   p95 1,190 ms
  ... minus one round trip  ~85 ms of actual index work
```

`search()` in hybrid mode issues **four sequential statements** — `BEGIN`,
`SET LOCAL hnsw.ef_search`, the fusion query, then `_fetch_rows` — and each pays
that ~250 ms round-trip from this Windows dev box to Neon in us-east. That is
where roughly 1.3 s of the 1.8 s goes. **The index does ~85 ms of the 1,776 ms,
or under 5%.** Partitioning, `iterative_scan`, PgBouncer and object storage
would improve *none* of the remaining 95%: it is deployment topology, not
architecture. Colocating the API with the database — which any real deploy does
— collapses the RTT and the whole search with it.

**So the trigger, which V4's first done-when box requires be defined before any
of it is built:**

* **Primary — `chunks` ≥ 1,000,000.** From the measured per-chunk cost that is
  ~8.6 GB of table and ~2.0 GB of HNSW, the point where the index stops being
  trivially resident. (The same rates reach ~129 GB / ~30 GB at 15M, which
  reproduces V2.md's paper figure and is the check that the rates are sane.)
* **Secondary — p95 of the *fusion statement* ≥ 250 ms, measured with the API
  colocated with the database.** Not end-to-end latency, and never from a remote
  dev box. Today's end-to-end p95 is 3,090 ms and means nothing about the index;
  a trigger phrased against it would have fired now, at 2,725 chunks, and bought
  a month of invisible waste. That is precisely the failure V2.md predicted for
  V4 — *"a partitioned index on a small corpus looks fine and buys nothing"* —
  arriving through the metric rather than through impatience.

**Incidental finding, not acted on:** the round-trip count is itself the real
latency lever at this scale, and it is not a V4 item. Four sequential statements
where the transaction wrapper and the `SET LOCAL` exist for the index tuning is
a fair trade when RTT is ~1 ms and a poor one when it is 250 ms. Worth measuring
again from a colocated deploy before anyone optimises it — the number may simply
disappear.

**Next per this ruling:** stay on V3's shape. V5 is *half* premature by the same
logic — per-user budgets and fairness gates for two users is V4's mistake in a
different costume — but its **answer cache on `(snapshot_id, question_hash)` is
valuable now**, not for load but for the provider rate limits recorded in
`app/agent/model.py` (20 requests/day/model on AI Studio). Pull that one
component forward when generation features start multiplying agent runs.

---

## 2026-07-31 — Deleted the test debris the broken teardown had been leaking

Direct consequence of the teardown bug in the entry above, and the evidence that
it was real rather than theoretical. The integration suites had been writing
throwaway corpora and deleting them from `repos` — a table no post-V2 ingest
touches — so every run since `007` left everything behind.

Removed: **16 sources, 18 snapshots, 70 chunks, 36 files, 70 symbols, and 7
`user_repos` rows.** The URLs date back to a Codespaces run
(`/tmp/pytest-of-codespace/...`), so this had been accumulating across machines.

The seven library rows are the part that mattered: those snapshots were in a
real user's library, which means `test_db_ingest_idempotent_and_0/mini` was
rendering in the signed-in repo list in the web app.

Predicate was `url LIKE '%pytest-of-%' OR url LIKE '%example.invalid%'` — tighter
than the `file:///%` first proposed, which could have matched a local repo
somebody ingested deliberately. Guarded by an assertion that it caught no
non-test source name, and by checking the benchmark corpus immediately before
and after: `825 | 697` both times.

---

## 2026-07-31 — The §18 UI, and the SQL that had never met Postgres

The two graph views shipped as endpoints with no consumer. That is worse than
not having built them: it reads as finished in SPEC and FEATURE-IDEAS and does
nothing for a user. Both now have a surface.

**Architecture panel** on `/repos/[id]`, below the chat CTA rather than above —
chatting is still the primary action, and this is orientation for a reader who
does not yet know what to ask. Modules ranked by fan-in with a bar relative to
the top module, because a bare number tells you nothing without the distribution
to compare it against; the bar *is* the distribution, and one hub with a long
tail looks different from a flat mesh at a glance. Each module expands into both
directions and offers a pre-filled question through the `?q=` route built the
same day — which is what turns a map into a starting point rather than a
diagram.

**Coverage strip** under the code-viewer header, collapsed by default and
rendered not at all when there is no linkage. The pane exists to show code, and
a permanently-open list would push the cited lines below the fold on a phone.
Open, every test is a button that moves the viewer to it, reusing the same
selection setter a citation click drives — so jumping to a test behaves exactly
like jumping to a cited range.

**The gap this closed, which the test suite could not have caught.** The §18
queries had **never been executed by Postgres.** `tests/api/test_graph_views.py`
runs against `FakeConn`, which routes statements by substring and returns
fixtures — it proves the route wiring, the response shaping, the caps, and the
tenancy checks, and it proves *nothing at all* about the CTEs, the correlated
subqueries in `module_nodes`, or the `(NOT is_test OR $2)` predicate. All four
functions were run against the live httpx corpus before this shipped:

```
23 modules, 120 module edges (impl only) · include_tests 23 -> 57
httpx/_exceptions.py   fan-in 80, fan-out 2     the leaf everything imports
httpx/_models.py       fan-in 71, fan-out 108   the hub
43x calls  _models.py -> _decoders.py           heaviest single pair
self-edges excluded: 0 · covers on an impl file: 0
tests/models/test_responses.py exercises 163 implementation symbols
```

The ranking being *recognisably correct* for httpx is the part worth recording.
A rollup that runs without error but ranks `setup.py` first would pass every
assertion in the suite; the check that it puts `_exceptions` and `_models` on
top is a human one, and it was made.

**Honest limitation, documented rather than hidden:** coverage is thin where
symbols are reached through a re-export. `_exceptions.py` links only 2 symbols
because tests mostly write `pytest.raises(httpx.ReadTimeout)` and the edge
resolves through `httpx/__init__.py`. That is real linkage, honestly partial —
these numbers are graph reachability, not coverage in the `coverage.py` sense,
and the panel should never be read as if they were.

---

## 2026-07-31 — 3.1 built: the overview is one model call, not an agent run

FEATURE-IDEAS' top pick, and the first feature that spends a model call outside
chat. SPEC §19 has the contract; this is why it is shaped the way it is.

**Deterministic gather, single synthesis — not the §7.2 loop.** The loop is the
right tool for a question nobody anticipated. An overview asks the same four
questions of every repo, and all four have exact answers in the symbol graph. So
SQL assembles the facts and one model call writes the prose. That buys three
things: one request per snapshot instead of eight, an input that is a pure
function of an immutable snapshot, and coverage of the *whole ranked graph*
rather than whatever an eight-call loop's first search happened to surface.

The cost point is not theoretical. `app/agent/model.py` records the AI Studio
ceiling at 20 requests/day/model. A loop here would make a handful of repo pages
a whole day.

**Lazy, and claimed by the primary key.** Generation runs on first view rather
than at the end of ingest — otherwise every ingest pays for an overview nobody
may open, and the seven snapshots already in the database would never get one.
Two browsers opening the same repo both attempt `INSERT … ON CONFLICT DO
NOTHING`; exactly one wins and only that one enqueues. No lock, no lease: on a
20-per-day budget, "exactly once" has to be a constraint rather than a
convention. Same argument as §15.3.

**Three defects found by running it, not by reading it.** All three were prompt
or data-shape problems that every test passed.

1. *2 of ~15 citations validated.* The model wrote
   `[httpx/_models.py:382-512,515-1076,139-379]` — the comma-separated form the
   chat prompt explicitly warns against. My overview prompt stated the same rule
   in its own prose and omitted the worked CORRECT/INCORRECT contrast. Sharing
   §7.5's `CITATIONS` block verbatim fixed it: **21 of 25, zero malformed**. The
   rule was present both times. The demonstration is what does the work — and
   one contract now lives in one place.
2. *Invented ranges.* Entry points were the one fact group with no line range,
   and the model did not decline to cite them — it wrote
   `[httpx/_transports/asgi.py:1-1]`. Nothing fabricated reached a reader
   (validation dropped them) but the claims lost their citations. Then the same
   thing happened one group over: with no range on modules it wrote the literal
   `[httpx/_models.py:1-?]`, which also *rendered*. **A fact you want cited has
   to arrive with something to cite.** Both groups now ship ranges; both are
   pinned by tests.
3. *The public-API signal was silent on the package style that needs it most.*
   Querying symbols *defined* in `__init__.py` returned **zero** for httpx,
   whose `__init__.py` is nothing but re-exports. Unioning in the `imports`
   edges out of `__init__.py` was the fix. Measured across the whole indexed
   corpus rather than assumed: markupsafe 25, itsdangerous 17, blinker 3, httpx
   1. httpx stays low because Jedi resolved only 2 of its re-export edges —
   a graph limitation, now documented rather than mistaken for a bug.

**What the prompt is forbidden to write, and why it is a rule rather than a
hope.** No installation, dependencies, configuration, or how-to-run. `*.py` is
all that is indexed, so there is no README, manifest, or CI config in the
corpus; anything on those topics would be the model recalling how projects like
this one usually work. That is exactly the failure this product exists to
avoid, and leaving it to chance in a section literally titled "how to run it"
would have been the most quotable possible own goal.

**Eval not run, and it does not apply.** Nothing here touches chunking,
retrieval, the embedder, or the agent's tool set — the loop is not involved at
all. The retrieval path is byte-identical.

**A note on cost discipline while building this.** Three live generations were
spent: the first exposed the citation-format defect, the second the placeholder
ranges, the third is the one stored. That is three of a twenty-a-day budget to
build the feature, which is the right order of magnitude to state out loud given
the whole design is about not spending them.

**Known trade-off, not fixed:** a dense run of citations renders as a wall of
near-identical chips (`httpx/_models.py:382-512` ×4 in one sentence). Honest and
each is clickable, so it ships — but if it grates, the fix is in the prompt
(cite the most important range per claim, not every range) rather than in the
renderer.

---

## 2026-08-01 — A second benchmark, and two of three claims did not replicate

The evidence base was one repository, twenty questions, two model families. That
was flagged repeatedly as the weakest part of the project and is now widened by
one repo: `pallets/flask` at `6a2f545b`, with its own twenty questions written
blind (`docs/EVAL-FLASK.md`).

**The repo was pre-registered, which is the only part of the method that could
not be added afterwards.** `ROADMAP.md` Phase 1 names exactly two candidates —
*"encode/httpx, pallets/flask"* — written before any retrieval code existed.
Taking the other name on that list is the one choice immune to "you picked a repo
that flattered the result". The questions were written from file listings and
`class`/`def` structure at the pinned SHA, **before ingest and before a single
query ran**, and every truth path and symbol was verified mechanically first: 20
questions, 0 ground-truth errors, 10/20 with zero lexical overlap against their
own answer symbols (httpx: 11/20, so comparable difficulty).

**Result: one of three claims replicated.**

1. **"Naive chunking ties AST" — reversed.** httpx: hybrid tied at hit@10 0.95
   with naive marginally ahead on MRR (0.759 vs 0.753). flask: **AST 0.95 vs
   naive 0.90, and 0.767 vs 0.720 on MRR.** The honest conclusion is not that
   AST wins — it is that a difference which changes sign between two repos, with
   every margin one or two questions at n=20, was never strong enough to carry
   what the README hung on it. Two repos now say the benchmark cannot
   distinguish them. The README has been corrected to say that.

2. **Hybrid fusion beating every single signal — fails.** This is the Phase 2
   gate, and it is the more consequential one. On flask, **plain vector search
   beats the shipped hybrid pipeline**: MRR 0.837 vs 0.767 on AST, and at every
   k on naive (0.90/0.90/0.95 vs 0.80/0.85/0.90). The gate as written —
   "default pipeline hit@10 ≥ every single-signal mode" — still passes on the
   AST corpus, but only by a three-way tie at 0.95, and would have failed on the
   naive corpus.

   Hypothesis, labelled as one: RRF fuses *ranks*, so it drags a strong ranking
   toward a weaker one. httpx's lexical leg is poor (MRR 0.463) so fusion could
   only add; flask's dense leg is excellent (0.837) so fusion could only dilute.
   If that holds, hybrid helps when the lexical signal is weak and hurts when the
   dense signal is strong — and which one a repo is cannot be known before
   measuring. **Not acted on.** Changing the default pipeline on n=2 repos would
   be exactly the over-reading this exercise exists to correct; it needs a third
   repo, and it is now a recorded question rather than an assumption.

3. **The ~20% unresolved-edge budget — does not hold.** httpx 4%, flask **52%**.
   SPEC §6.1 now carries the correction. This is the finding that matters most,
   because the symbol graph is what the project claims over plain retrieval, and
   on flask it is less than half as dense per symbol. Cause not diagnosed;
   `src/`-layout breaking Jedi's project root is the suspect but two flat repos
   also sit low, so it is unestablished and recorded as open.

**Two defects found in the measuring apparatus itself**, both of the same family
as the ones this session has been turning up — correct when written, silently
wrong later, never failing:

* `eval.py` read `head_sha` from the pre-V2 `repos` table, which V2 stopped
  writing. It returned NULL for every post-`007` ingest, so the
  "ingested commit != pinned commit" warning — the one check standing between a
  result block and the wrong corpus — could not fire. Now reads
  `repo_snapshots.commit_sha`.
* `eval.py` crashed on Windows *after* completing a full measurement, encoding
  `✓` to a cp1252 console, losing the run between computing and appending. Now
  reconfigures stdout.

**What this does not cover, stated so it is not assumed:** answer-level eval
(agent vs stuffed) was **not** run on flask — ~40 model calls against a 20/day
tier — so Phase 3's findings (a)/(b)/(c) remain httpx-only and are neither
confirmed nor disconfirmed. `hybrid+rerank` was not measured on flask either.
And n is still 20 per repo: the value here is that the *sign* flipped on two
claims, which no additional precision on a single repo could ever have revealed.
