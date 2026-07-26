# Phase 2 retrieval — failure analysis & options (for review)

**Status:** Phase 2 ("Store & retrieve") of a codebase-onboarding RAG app is
built and runs, but its acceptance gate ("done-when") is **not met**. This
document explains precisely what failed, the evidence, why, and the concrete
options to move forward. It is self-contained — no repo access needed to review.

---

## 1. What Phase 2 is supposed to do

Ingest a public GitHub repo (Python only, v1), then answer questions about it
with citations. Phase 2 is the **retrieval** layer only (no LLM agent yet — that
is Phase 3). The pipeline:

1. **Chunk** the code on AST boundaries (tree-sitter): one chunk per function /
   method / class-skeleton / module, each with an enrichment header
   (file path, dotted symbol name, signature, imports).
2. **Embed** each chunk with a bi-encoder (`BAAI/bge-small-en-v1.5`, 384-dim,
   cosine) and store in Postgres + `pgvector` (HNSW index), alongside a Postgres
   full-text-search (FTS) `tsvector` column.
3. **Retrieve** via one hybrid query — `hybrid_search()`:
   - **vector** leg: top-40 by cosine similarity.
   - **FTS** leg: top-40 by `ts_rank` using `plainto_tsquery` (AND semantics).
   - **RRF fusion**: combine the two ranked lists with Reciprocal Rank Fusion
     (`score = Σ 1/(60 + rank)`), one SQL statement. → fusion top-40.
   - **Exact-symbol injection**: pull identifier-like tokens from the query
     (things with `_`, a dot, or CamelCase) and add any chunks whose dotted
     symbol matches, to catch "where is `verify_token` defined".
   - **Rerank**: score every candidate `(query, header+code)` pair with a
     cross-encoder (`BAAI/bge-reranker-v2-m3`), return the top-10.

## 2. The benchmark and the metric

- **Corpus:** `encode/httpx` pinned at commit `b5addb64…`. The ingest cloned
  the repo and, by luck, `master`'s HEAD is *still exactly that pinned commit*,
  so there is **no version drift** (a path-drift guard confirmed every
  ground-truth file is present). 60 files → **1522 chunks**.
- **Ground truth:** 20 frozen questions, each with ≥1 acceptable answer file
  (and optional symbol names). Three tiers: `locate` (q01–q07, easy — the
  identifier is in the question), `conceptual` (q08–q15, "how does X work"),
  `flow` (q16–q20, "what happens when…"). 11 of 20 have **zero lexical overlap**
  between question and answer symbols — deliberately, to punish pure keyword
  search.
- **Metric:** `hit@k` (k = 5, 10). A question "hits" if any of the top-k results
  is in the answer file set (or matches an answer symbol). This is a **recall**
  metric: did the right chunk make the top-k at all.
- **Acceptance gate (done-when):** *"hybrid+rerank hit@10 ≥ every single-signal
  mode."* Single-signal = vector-only and FTS-only. In plain terms: **the full
  pipeline must not do worse than its simplest part.**

## 3. The result

Eval over all 20 questions, four modes:

| Mode                     | hit@5        | hit@10         |
|--------------------------|--------------|----------------|
| vector (single-signal)   | 0.80 (16/20) | **0.85 (17/20)** |
| fts (single-signal)      | 0.05 (1/20)  | 0.05 (1/20)    |
| hybrid (RRF, no rerank)  | 0.80 (16/20) | 0.85 (17/20)   |
| **hybrid+rerank** (prod) | 0.75 (15/20) | **0.80 (16/20)** |

**Gate fails:** `hybrid+rerank@10 = 0.80` is **below** `vector@10 = 0.85`.
The full pipeline is *worse* than the vector leg alone.

Per-question hit@10 (✓ = hit): the only differences are

- `vector`/`hybrid` hit **17**: everything except **q09, q10, q15**.
- `hybrid+rerank` hits **16**: everything except **q09, q10, q14, q15**.

So the pipeline loses exactly **one** question versus vector: **q14**
("How does httpx turn a streamed byte body into a string as chunks arrive?",
answer: `TextDecoder` in `httpx/_decoders.py`). It also loses one at hit@5
(15 vs 16).

## 4. What actually failed — diagnosis

**(a) Fusion, injection, and the vector leg are all sound; the reranker is the
hit@10 regression — but the FTS leg is separately, more seriously broken (below).**
`hybrid` (RRF fusion, *no* rerank) equals `vector` at 0.85. The hit@10 regression
(17 → 16) is entirely the cross-encoder rerank. But `fusion == vector` *to the
decimal* is itself a red flag — investigated in §4bis.

### 4bis. The FTS leg contributes nothing — confirmed root cause

FTS-only scores 0.05 (1/20). Direct DB inspection (via `psql`, no models) shows
the **plumbing is sound** and the cause is **query construction**:

- The `tsv` column is populated (`Timeout` chunk 722 chars, `urlparse` 1193, both
  non-null) and the GIN index exists (`chunks_tsv … USING gin (tsv)`). No type
  mismatch, no NULL tsquery.
- The query builder uses **`plainto_tsquery`, which ANDs every term.** A full
  question becomes a conjunction of *all* its stemmed words:
  - q01 → `'request' & 'timeout' & 'configur' & 'class' & 'defin'` → **0 chunks**
    match all five (no code chunk contains every sentence word).
  - q03 → `'url' & 'pars' & 'function' & 'urlpars' & 'implement'` → **0**.
- Yet the *key* term alone matches plenty: `to_tsquery('timeout')` → 295 chunks;
  `to_tsquery('urlparse')` → 73. The truth chunks **do** contain their key term
  (`Timeout` chunk matches `timeout`, `urlparse` chunk matches `urlparse`) — they
  are only excluded because the surrounding question words are ANDed in.
- Consequence: **fusion's FTS CTE returns 0 rows for essentially every question**,
  so RRF degenerates to vector-only — hence `fusion == vector == 0.85` exactly.
- `websearch_to_tsquery` also ANDs unquoted terms → also 0. **OR-combining the
  same lexemes** (`to_tsquery('request | timeout | … ')`) returns 845 (q01) / 1227
  (q03) chunks, with the truth chunk ranked #21 / #10 — inside `FTS_K = 40`, so
  RRF *would* get a real lexical signal.

**This is not "weak FTS," it is a dead FTS leg** caused by `plainto_tsquery`'s
AND semantics being unsatisfiable for verbose NL questions over code. The
`locate` tier isn't rescued by it either, because the question is a full sentence,
not a bare symbol — literal-symbol matching is the job of §5.2 **injection**
(which matches `chunks.symbol` directly), not FTS. Fixing the FTS query
construction (OR-combine, or extract salient terms, possibly weighted) is likely
the **highest-value change available** — it is why the hybrid never beats vector,
and it would give the reranker a lexical signal to fuse rather than a vector-only
pool. See Option E.

**(b) The reranker had the right chunk and still demoted it.**
For q14, the `TextDecoder` chunk is ranked in the **top-10 by the vector leg**
(vector hits q14), so it is unquestionably in the reranker's candidate pool. The
cross-encoder then scored 10+ other chunks higher, pushing it past rank 10. In
other words: **the bi-encoder's cosine similarity was a better top-10 signal for
this question than the cross-encoder's relevance score.** (An earlier spot-check
on the easy q01 showed the same shape: the reranker put `BaseClient` above the
obviously-correct `Timeout` class.)

**(c) Why a reranker is structurally unlikely to *win* hit@10.**
A reranker does not add candidates — it only **re-orders a fixed pool**. It can
therefore only *improve* hit@k if its ordering places a truth chunk in the top-k
that the fusion ordering placed *below* k. At k=10 that head-room is tiny
(fusion already gets 17/20 into the top-10), while the **downside** — demoting a
correct chunk from rank ≤10 to rank >10 — is fully available. Cross-encoders are
built to win at **low k / MRR** (getting the single best answer to rank 1–3),
which is precisely the signal Phase 3's agent needs to pick an entry point — and
which **this gate does not measure.** So the metric is, arguably, testing the
reranker where it is weakest.

**(d) The three questions everything misses are a different problem.**
`q09, q10, q15` are missed by **every** mode including vector — their answer
chunk is not even in the fused pool. These are the hard "conceptual/flow"
questions (e.g. compression handling, multipart body building, charset origin).
Retrieval alone cannot reach them; closing this gap is exactly the thesis of
**Phase 3** (an agent that traverses the symbol graph — imports/calls — to reach
code the retriever missed). They are out of scope for the Phase 2 gate and are
*not* the reranker's fault.

**Summary:** nothing is broken in fusion/embedding/injection. The reranker, a
general-purpose multilingual passage model applied to Python code, mildly
mis-orders borderline conceptual-query chunks and nets **−1 question at hit@10,
−1 at hit@5** relative to plain fusion. The gate as written can essentially only
be met if the reranker *never* demotes a top-10 truth chunk — a high bar for a
recall metric.

## 5. A second, environmental failure (verification blocked)

The dev machine has **8 GB RAM**. The embedding model is small (~130 MB) and
fine, but the **reranker is ~2.4 GB in memory**. The first eval run completed
(~8 min, slow from swapping). After that, the machine stayed swap-saturated
(~1.9 GB swap in use, near-zero free RAM), and **every subsequent process that
loads the reranker blocked at import/startup** — sleeping at ~0 % CPU with torch
never resident, i.e. thrashing on swap I/O, not computing.

Consequences:
- `debug_search.py` (the per-signal inspector) could not finish a run here.
- The **unit + integration tests could not be executed** on this host (even the
  no-torch tests stalled once swap was saturated). They are **written** and
  ruff-clean; they need a run on a healthier machine.
- Mitigation applied: the embedder module now imports `sentence_transformers`
  **lazily** inside its factory functions, so importing the retrieval code or
  the pure-logic tests no longer drags in torch. This makes the unit tests
  runnable without the 2.4 GB load; only the marked integration test needs it.

This is an environment limitation, not a code defect, but it means **any fix
below must be validated on a host that can hold the reranker in RAM** (≥16 GB,
or a GPU box, or a smaller reranker).

## 6. Options to continue (for the reviewer to weigh)

All model-running options require a capable host.

**Option A — Fusion floor / score blend (make the pipeline monotonic).**
Change the rerank step so the returned top-k can never be *worse* than pure
fusion: e.g. final order = `α·norm(cross_encoder) + (1−α)·norm(rrf)`, or simply
guarantee the top-N fusion hits are retained. This makes `hybrid+rerank ≥ hybrid`
by construction, so the gate would pass. *Cost:* deviates from the current spec
("return top-k by cross-encoder score") — needs a design decision recorded, and
a value for `α` chosen on a validation signal (not hand-tuned to q14). Re-run the
full eval to confirm.

**Option B — Measure the reranker where it can win (low-k / MRR).**
Add `hit@1`, `hit@3`, and MRR to the eval and re-run. If the reranker improves
those (very likely — that's what cross-encoders do), you have evidence its value
is precision for the Phase 3 agent's entry point, and you can consciously accept
that it doesn't help hit@10. This reframes the gate rather than changing the
algorithm.

**Option C — Accept fusion-only hybrid as the Phase 2 baseline.**
`hybrid` (no rerank) already meets the bar (0.85 ≥ 0.85). Ship Phase 2 with
rerank *off* (or behind a flag) and revisit reranking in Phase 3 where low-k
precision actually matters and can be measured against agent answer quality.
Cheapest path; defers the reranker question.

**Option D — Swap the reranker.** The current model is general-purpose and
multilingual. A code-aware or smaller reranker might both fit in less RAM and
rank code better. Larger change; needs the same validation.

**Option E — Fix the FTS query construction (likely the biggest lever). [APPLIED
2026-07-25]** Implemented: the §5.1 FTS leg now OR-combines `plainto_tsquery`'s
english-stopword-stripped, stemmed lexemes (swap `&`→`|`) instead of ANDing them.
Result: **fts hit@10 0.05 → 0.65 (13/20)**, MRR 0.273 — the leg is now a real
recall signal. Caveat proven on q01/q03: the truth-chunk rank stays #21/#10
because the residual diluters (`class`, `function`, `defined`, …) are *content
words*, not english stopwords; the single-salient-term rank is #2/#9. Whether
this makes `hybrid` beat `vector` at hit@10 is **not yet measured** — vector and
hybrid need the embedder (torch), which the 8 GB host can't load. Original
proposal follows.
Replace `plainto_tsquery` (AND) with an OR-combination of the query's salient
lexemes (or extract identifier/keyword terms and OR them, optionally weighted by
`setweight`/`ts_rank_cd`). Evidence (§4bis) shows this turns the FTS leg from 0
rows into a ranked list that contains the truth chunk within `FTS_K`, so RRF
finally fuses a real lexical signal instead of collapsing to vector-only. This is
a §5.1 change (needs a SPEC reconciliation + DECISIONS entry) and a full re-run,
but it is the most likely way to make `hybrid` actually beat `vector` — and it
gives the reranker a better pool to work from. **This does *not* need the
reranker to iterate** (measure `--mode fts` and `--mode hybrid`), so it can be
developed on the 8 GB host.

**Recommended sequence:** **E first** (fix the dead FTS leg — highest value, and
iterable without the reranker), then **B** (measure the reranker at hit@3/MRR now
that eval.py reports them), then **A or C** for the reranker decision. Avoid
changing the reranker before FTS is fixed and low-k numbers exist, or you risk
optimizing the wrong component against the wrong metric.

> **Note:** `eval.py` now reports **hit@3, hit@5, hit@10, and MRR** (added
> 2026-07-25; hit@5/@10 unchanged). hit@3/MRR are the columns that reveal
> reranker behavior; they need a capable host to populate for the rerank mode,
> but the metric code is unit-tested and runs model-free for `--mode fts`.

## 6bis. Test shadowing — diagnosis and a PRE-REGISTERED prediction

**Written 2026-07-26, BEFORE the exclusion eval was run.** Recorded in advance so
the mechanism story is falsifiable rather than fitted after the fact.

### The finding

The 2026-07-26 full eval (first run on a host that can hold the reranker) failed
the gate harder than §3's run: `hybrid+rerank` hit@10 **0.75** vs `vector`
**0.85**, with `hybrid` at **0.80**. Fixing FTS raised the leg standalone
(0.05 → 0.65) yet *lowered* both fused modes. Per-signal inspection of the two
regressing questions found one cause:

- **q14** (`TextDecoder`): 9 of the FTS top-10 are `tests/`. Vector hits only via
  a file-level match at **rank 10 exactly** (`ByteChunker.decode`); RRF pulls test
  noise in and displaces it. Lost at **fusion**.
- **q08** (`BasicAuth`): vector top-10 is 8/10 `httpx/_auth.py`; FTS top-10 is
  9/10 `tests/`. After rerank `BasicAuth` is **absent**, and the cross-encoder's
  #1 is `tests/test_auth.py::test_digest_auth_with_401_nonce_counting`. Lost at
  **rerank**.

**Mechanism:** test files are written in *user vocabulary* ("chunk", "stream",
"auth") in prose-like names and assertions; implementation code is terse and
identifier-dense. Both the lexical leg and a general-purpose passage-relevance
cross-encoder therefore score tests as more relevant to a natural-language
question than the code that implements the answer. The FTS fix did not cause
this — it *exposed* it, by giving test chunks their first real path into fusion.

### The intervention

Flag-and-filter (SPEC §2.6 + §5.4): classify chunks as `is_test` by a
corpus-wide path rule at ingest; exclude them from both fusion CTEs and from
§5.2 injection by default. Tests remain in the corpus and in `files`.

### The prediction (falsifiable)

If test shadowing is the mechanism, exclusion should produce:

1. **`fts` rises sharply, most at low k** — its top ranks were nearly all tests,
   so removing them promotes implementation into the visible window.
2. **`hybrid` and `hybrid+rerank` rise the most** — they were the *polluted*
   modes; both regressions above occur in fused/reranked stages.
3. **`vector` stays roughly flat, or rises slightly** — the vector leg was never
   test-dominated (q08: 8/10 implementation; q14: implementation throughout), so
   it has little pollution to remove. Small gains are expected where a test chunk
   held a top-10 slot, but not gains comparable to the fused modes.

**Falsifier:** *if `vector` jumps as much as the fused modes, the mechanism story
is weak* — that would indicate exclusion is simply shrinking the candidate space
in the benchmark's favour (all 20 truth files are implementation), not correcting
a fusion/rerank-specific pathology. Report it straight either way.

**Not predicted / out of scope:** q09, q10, q15 are missed by every mode because
their answer chunk is not in the pool at all. Exclusion is not expected to fix
them; that is Phase 3's graph traversal.

**Caveat recorded up front:** all 20 truth files are implementation, so this
change raises measured scores *by construction*. The justification is product
intent and mechanism generality, not the score. The `--include-tests` flag keeps
the counterfactual measurable.

### 6ter. OUTCOME — how the prediction fared (added after the run, 2026-07-26)

Both conditions measured in one run (`--both-conditions`), same corpus, 1522
chunks (825 implementation / 697 test). Δ = implementation-only − shadowed.

| Mode | hit@10 shadowed | hit@10 impl-only | Δ@10 | Δ@3 | Δ MRR |
|---|---|---|---|---|---|
| vector | 0.85 | 0.90 | **+0.05** | +0.10 | +0.090 |
| fts | 0.65 | 0.80 | +0.15 | **+0.30** | +0.194 |
| hybrid | 0.80 | **0.95** | **+0.15** | +0.10 | +0.138 |
| hybrid+rerank | 0.75 | 0.85 | +0.10 | +0.10 | +0.118 |

1. **Confirmed.** `fts` rose sharply and most at low k — hit@3 0.25 → 0.55
   (+0.30), the largest single delta in the table.
2. **Confirmed.** The fused modes gained more than `vector` (+0.15 / +0.10 vs
   +0.05 at hit@10); `hybrid` gained the most of any mode at hit@10.
3. **Confirmed.** `vector` rose least of all four modes — exactly the "roughly
   flat, slightly up" shape predicted.

**Falsifier NOT triggered.** `vector` gained +0.05 against `hybrid`'s +0.15, so
the gains are concentrated in the modes the mechanism says were polluted. The
test-shadowing explanation stands on its own pre-registered terms.

**One prediction missed, in the favourable direction.** q09 and q15 were written
off above as pool-absent / Phase-3-only. Both were in fact partly test-shadowed:
q15 now hits in **all four** modes and q09 in `fts`/`hybrid`. Only **q10** remains
missed by every mode — genuinely Phase 3 territory. The §4(d) claim that all
three were beyond retrieval was too pessimistic.

**The gate still fails.** `hybrid+rerank` hit@10 **0.85 < `vector` 0.90**. The
regression *relocated* rather than closed: `hybrid` alone is now **0.95 (19/20)**,
missing only q10, and the cross-encoder demotes q09 and q14 back out of a top-10
that fusion had already found — 0.95 → 0.85. This sharpens §4(c): with a clean
implementation-only pool the reranker's downside is now the *only* thing between
the pipeline and the gate.

**New option, previously dead.** `hybrid` (0.95) clears every single-signal mode
(`vector` 0.90, `fts` 0.80), so Option C — ship fusion-only, rerank off — would
now **pass** the gate as written. It was dead before exclusion (§5 of this doc's
earlier revision) because `hybrid` was 0.80 < `vector` 0.85. Recorded as
evidence; not adopted without sign-off.

### 6quater. Cross-encoder scores on the two demoted questions (evidence)

Run on the default implementation-only pool (40 candidates), 2026-07-26. The
mechanism claim — a general-purpose passage model rewards question-vocabulary
overlap over terse implementation — is measurable, so here it is measured.

**q09 — "How does httpx handle responses the server has compressed?"**
Truth: `httpx/_decoders.py`. Three truth chunks are in the pool.

| | CE score | CE rank | fusion rank |
|---|---|---|---|
| `_decoders.ZStandardDecoder` (truth) | **+0.0468** | **38** | **4** |
| `_decoders.BrotliDecoder.__init__` (truth) | +0.0783 | 37 | 37 |
| `_decoders.ZStandardDecoder.__init__` (truth) | +0.0174 | 40 | 38 |
| `_models.Response.aiter_bytes` | +0.6666 | 1 | 6 |
| `_models.Response.iter_bytes` | +0.6535 | 2 | 7 |
| `_transports.default.HTTPTransport.handle_request` | +0.5271 | 3 | 16 |

Fusion put a truth chunk at **#4**; the cross-encoder pushed it to **#38** — out
of the top-10 by 28 places. The winning chunks are `Response.*` and
`*.handle_request`: their names and docstrings share surface vocabulary with the
question ("response", "handle"), while the code that actually decompresses is a
terse decoder class whose body is `self.decompressor = ...`. The CE scores the
truth chunks at **+0.017 … +0.078** against **+0.667** for the top distractor —
roughly an order of magnitude, in the wrong direction.

**q14 — "How does httpx turn a streamed byte body into a string as chunks arrive?"**
Truth: `httpx/_decoders.py`. Four truth chunks in the pool.

| | CE score | CE rank | fusion rank |
|---|---|---|---|
| `_decoders.ByteChunker.decode` (truth) | **+0.3782** | **20** | **5** |
| `_decoders.ByteChunker` (truth) | +0.1388 | 31 | 14 |
| `_transports.asgi.ASGITransport.handle_async_request` | +0.6568 | 1 | 31 |
| `_models.Response.aiter_raw` | +0.6168 | 2 | 21 |
| `_models.Response.iter_raw` | +0.6107 | 3 | 19 |
| `_models.Response.iter_text` | +0.6019 | 4 | 29 |

Same shape: fusion **#5** → CE **#20**. The chunks that displace it are
`iter_raw` / `iter_text` / `aiter_raw`, whose identifiers echo the question's
"streamed", "string", "chunks" almost word-for-word. Note the inversion in the
fusion column — the CE promoted chunks fusion had ranked #31, #21, #19, #29 over
one it ranked #5.

**Reading.** In both cases the reranker is not failing at the margin: it is
inverting a correct fusion ordering by a wide margin, and the chunks it prefers
are the ones whose *surface vocabulary* matches the question. This is the
NL-vs-terse-code mechanism as concrete evidence rather than narrative, and it is
the same phenomenon as test shadowing (§6bis) acting on implementation code —
prose-like beats terse, regardless of which is correct.

## 7. Specific questions for the reviewer

1. Is hit@10 the right acceptance gate for a component whose job is low-k
   precision? Should the done-when be restated (e.g. "rerank must not *reduce*
   hit@10, and must *improve* MRR")?
2. Is a "fusion floor" (Option A) a principled fix or a metric hack? (It
   guarantees monotonicity but partially neuters the reranker.)
3. Given the 8 GB constraint, is a smaller reranker (Option D) worth prioritizing
   so the whole loop is iterable on modest hardware?
4. Are q09/q10/q15 acceptable to leave to Phase 3, or should Phase 2 retrieval be
   pushed (e.g. query expansion, larger pools) to reach some of them first?

## 8. Reproduce on a capable host

```bash
cd backend
uv sync
uv run python scripts/migrate.py                              # applies 001+002
uv run python -m app.ingest.cli https://github.com/encode/httpx --db
uv run python scripts/eval.py --mode all                      # appends to docs/EVAL.md
uv run python scripts/debug_search.py --repo https://github.com/encode/httpx \
    --query "How does httpx turn a streamed byte body into a string as chunks arrive?"
uv run pytest -m "not integration"                            # unit tests (no model)
uv run pytest -m integration                                  # needs DB + reranker RAM
```

The gate check is the `hybrid+rerank` vs `vector` row of the `--mode all` table.


---

## Appendix — M2 trace backfill gap (2026-07-26)

The first live agent run (`gemini-3.5-flash`, "How does httpx decide which
transport to use for a request?") completed successfully: **8/8 tool calls, 16
validated citations, 93.1s**, correctly identifying `_transport_for_url`, the
mount-pattern match, the `None` → default-transport fallback, and
specificity-ordered mounts across both the sync and async client paths.

**Its tool-call trace was lost** — truncated by a `tail` on the way to the
terminal — and the AI Studio daily quota (20 requests/model) was exhausted by
the time the loss was noticed, so it could not be reproduced that day. The
answer and citation list survive in the M2 report; the per-call trace does not.

Fixed at the source rather than retried: the agent CLI now always writes the
complete trace to `backend/var/traces/` before printing (DECISIONS 2026-07-26,
"Agent traces are always persisted to disk"). A substitute full trace on
`gemini-3.5-flash-lite` is recorded in the M2 report.

---

# Phase 3 / M3 — dev questions and tuning log

Tuning is measured against `docs/dev-questions.yaml` (7 questions, authored
2026-07-26, truth verified against the ingested symbol graph). The frozen 20
in EVAL.md are never used for tuning — a prompt iterated against them stops
measuring anything.

## Baseline (before any tuning) — `mistral-medium-latest`

| Mode | answer-hit | cited | tool calls (mean/max) |
|---|---|---|---|
| stuffed | 0.86 (6/7) | 0.86 (6/7) | — |
| agent | 1.00 (7/7) | 1.00 (7/7) | 4.4 / 8 |

The agent's only win was **d06** ("files= and data= together"), a flow
question whose answer spans `_content.py` and `_multipart.py`.

## Run-to-run variance — measured, and it matters

The identical agent configuration scored **7/7 and then 5/7** on the same
seven questions (d02 and d04 flipped), at `temperature=0.0`. Mistral is not
deterministic at temperature zero.

**Consequence for how much weight results can bear:** a 7-question dev set
cannot resolve a delta smaller than roughly ±2 questions, and even the frozen
20 will carry visible noise. This is the reason the M3 plan requires the
stuffed-vs-agent pattern to hold on *two independent models* — a delta that
appears once is not evidence.

## Tuning iteration 1 — prescriptive tool descriptions

**What changed.** Tool descriptions rewritten to state *when* to call each
tool, not merely what it does — e.g. `expand_context` now says "call this
whenever the question is about a flow, a sequence, or 'what happens when'",
and `search_code` explicitly says not to re-run with reworded phrasing.

**Why.** The first instrumented dev run showed the graph-traversal tools at
**0% usage**: `read_file` 52%, `search_code` 42%, `get_definition` 6%,
`expand_context` 0%, `find_references` 0%. The agent was doing retrieval with
extra steps — the mechanism the thesis rests on was never invoked. Trigger
conditions in a tool description are the documented lever for should-call rate.

**Observed effect.**

| Tool | before | after |
|---|---|---|
| `read_file` | 52% | 49% |
| `search_code` | 42% | **29%** |
| `get_definition` | 6% | **17%** |
| `expand_context` | **0%** | **6%** |

Re-searching fell, direct symbol lookup and graph traversal rose. Answer-hit
went 5/7 → 7/7, but given the variance above **that number is not claimed as
the effect** — the tool-mix shift is the reliable signal.

**`find_references` remains at 0%, and that appears correct.** None of the
seven dev questions asks "what uses X" / "where is X invoked from", which is
the condition its description now names. Its absence here is the tool not
being needed, not the tool not being reachable. Worth re-checking on the
frozen 20, which contains flow questions that may call for it.

**Not attempted:** further prompt iterations. With ±2 questions of noise on a
7-question set, additional tuning would be fitting sampling noise rather than
improving behaviour.
