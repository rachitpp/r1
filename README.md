# Codebase Onboarding Assistant

Submit a public GitHub repo → it clones, chunks the code on AST boundaries
(tree-sitter), embeds the chunks, and builds a symbol graph of imports, calls,
and inheritance. Ask *"how does auth work?"* and a LangGraph agent finds entry
points with hybrid retrieval, then traverses the symbol graph to pull in the
dependent code retrieval missed — answering with `file:line` citations.

**Stack:** Python 3.11+, FastAPI, Postgres 16 + pgvector, tree-sitter, Jedi,
LangGraph. Benchmark: [`encode/httpx`](https://github.com/encode/httpx) pinned
at `b5addb64`, 20 frozen questions with ground truth.

---

## The claim, and exactly how strong it is

The thesis is **"retrieval finds entry points; graph traversal finds the
answer."** It was tested against a *stuffed* baseline — one model call with the
top-10 retrieved chunks and the same citation contract — on 20 frozen questions,
across two model families. Three findings, ranked by the strength of their
evidence rather than by how good they sound:

### (a) STRONG — the graph reaches what retrieval cannot

**q10 is answered by the agent and missed by the baseline in every run, on both
models.** It is the one question missed by *every* retrieval mode — vector,
FTS, hybrid, hybrid+rerank — in *both* corpus conditions across the whole
retrieval phase. **q14** behaves the same way on Vertex under the stricter
metric.

This is the falsifiable core of the thesis, and it holds.

### (b) MODERATE — a directionally stable symbol-level lead

The agent leads the baseline at symbol level in **6 of 6 runs across two model
families**:

| Model | run margins | agent mean | baseline |
|---|---|---|---|
| `mistral-medium-latest` | +5 / +4 / +2 | 0.93 (0.85–1.00) | 0.75 |
| `vertex:gemini-2.5-flash` | +1 / +1 / +2 | 0.87 (0.85–0.90) | 0.80 |

**The sign is stable; the magnitude is noisy.** Mistral spans 0.85–1.00 across
identical configurations at temperature 0. Six positive runs is evidence of
direction, not of effect size.

### (c) NOT SUPPORTED — graph-tool use does not predict correctness

The obvious mechanism story — *the agent wins because it uses graph tools* —
**does not hold.** At identical temperature, the two models invert:

| | with graph tool | without graph tool |
|---|---|---|
| Mistral | 20 hit / **0 miss** | 36 hit / 4 miss |
| Vertex | 28 hit / **6 miss** | 24 hit / **0 miss** |

Every Mistral miss came from a run using no graph tool; every Vertex miss from
a run that did. This is a **selection effect** — the agent reaches for graph
tools on the questions it finds hard, and those differ by model.

An earlier single-run cross-tab of 7/7 was reported as the mechanism made
visible. It did not replicate. It is retracted and recorded as such in
`docs/DECISIONS.md`.

---

## What the headline numbers do *not* show

The aggregate file-level scores (0.90–0.95 for both modes) look like a tie, and
quoting them as the result would be **quoting the retrieval pipeline's hit@10
twice**. The file-level metric asks only whether one cited *file* is correct,
and the baseline is handed a top-10 pool whose hit@10 is 0.95 — so "the right
file was in the context window" and "the model assembled an answer" score
identically. **19 of 20 questions had no discriminating power under it.**

That is why a symbol-level metric was added: it requires the answer to
demonstrably *name* the construct, which a retrieved pool cannot supply by
accident the way it supplies a filename. Findings (a) and (b) rest on it.

Two more results that cut against the project's own expectations:

- **The flow tier (q16–q20) ties everywhere.** The phase plan predicted
  cross-file "what happens when…" questions would favour the agent. They
  didn't — a ten-chunk pool at 0.95 hit@10 already contains what they need.
  That is a finding about the benchmark, not the agent, and harder cross-file
  questions are a v2 item.
- **Temperature was not controlled across providers** until late: Mistral ran
  at 0 while Gemini/Vertex used the provider default of 1.0. All four providers
  are now pinned to 0, and the six repeat runs are the first like-for-like
  cross-model comparison in the project. Earlier results reproduce
  qualitatively but were not controlled.

---

## Measured pipeline results

Retrieval (Phase 2), `encode/httpx`, 1522 chunks:

| Mode | hit@3 | hit@5 | hit@10 | MRR |
|---|---|---|---|---|
| vector | 0.75 | 0.85 | 0.90 | 0.722 |
| fts | 0.55 | 0.70 | 0.80 | 0.465 |
| **hybrid** (default) | **0.80** | **0.90** | **0.95** | **0.755** |
| hybrid+rerank | 0.80 | 0.80 | 0.85 | 0.722 |

The cross-encoder reranker is **off by default**: it measured worse-or-equal to
plain RRF fusion at every k and at MRR, in both corpus conditions, for ~2.4 GB
resident. It stays wired so the ablation remains measurable.

Test chunks are flagged at ingest and excluded from retrieval by default —
they were 46% of the corpus and systematically outranked implementation for
natural-language questions.

Symbol graph: 1201 symbols, 2304 edges, 4% Jedi resolution failure (~20%
budgeted), 34s pass.

Full tables, per-question grids, and every decision: `docs/EVAL.md` and
`docs/DECISIONS.md`.

---

## Running it

```bash
docker compose up -d                      # postgres + redis
cd backend && uv sync
cp .env.example .env                      # add DATABASE_URL and a model key
uv run python scripts/migrate.py
uv run python -m app.ingest.cli https://github.com/encode/httpx --db
uv run python -m app.agent.cli https://github.com/encode/httpx "How does httpx pick a transport?"
```

The agent model is provider-configurable via `AGENT_MODEL` — prefix selects the
client (`mistral` / `gemini` / `claude` / `vertex:`), built only by
`app/agent/model.py`.
