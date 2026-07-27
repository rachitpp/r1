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
