# Phase 2 prompt — Store & retrieve

> **How to use:** save as `docs/prompts/phase-2.md`, start a fresh
> Claude Code session at the repo root, and instruct: "Read
> docs/prompts/phase-2.md completely, confirm Phase 1 is done in
> ROADMAP.md, give me a ≤10-line plan, then proceed."

---

You are starting **Phase 2 — Store & retrieve** of this project.

## Step 0 — Orient

Read, in this order:
1. `CLAUDE.md`
2. `docs/ROADMAP.md` — the Phase 2 section
3. `docs/SPEC.md` — §3, §4, §5, §10 (the delete-and-replace idempotency
   concept only), §11, §12
4. `docs/DECISIONS.md` and `docs/EVAL.md` (note the frozen rule and the
   symbol match rule recorded there)

Confirm Phase 1 is `done`. Plan in ≤10 lines, then proceed.

## Step 0.5 — Environment gates (in this order, before building)

**Gate A — Postgres reachable.** Phase 2 needs only Postgres — Redis is
not used until Phase 4.

```bash
cd backend
uv run python scripts/migrate.py
```

If this cannot connect: **STOP** and tell the human to either start
Docker (`docker compose up -d`) or provision a free Neon database and
set `DATABASE_URL` in `backend/.env` (Neon DSNs need
`?sslmode=require`). Do not mock the database or proceed without it.

**Gate B — torch / sentence-transformers under WDAC.** This host blocks
some unsigned native binaries (ruff yes, tree-sitter no). torch is the
biggest native payload yet:

```bash
uv add sentence-transformers pgvector pyyaml
uv run python -c "import torch; print('torch', torch.__version__)"
uv run python -c "from sentence_transformers import SentenceTransformer; \
m = SentenceTransformer('BAAI/bge-small-en-v1.5'); \
v = m.encode(['hello world'], normalize_embeddings=True); \
print('embed OK', v.shape)"
```

- DLL-load / policy error → **STOP.** Report the exact error; the fix
  is WSL2 or another machine, not a workaround.
- Download/network error → **STOP** and report it as a *network* issue
  (distinct from WDAC) — HuggingFace access may be restricted.

**Gate C — reranker.** `BAAI/bge-reranker-v2-m3` is a ~2 GB download;
warn, then:

```bash
uv run python -c "from sentence_transformers import CrossEncoder; \
r = CrossEncoder('BAAI/bge-reranker-v2-m3', max_length=512); \
print('rerank OK', r.predict([('query', 'passage')]))"
```

Same stop rules as Gate B. All three gates green → continue.

## Session rules

- Build **only Phase 2**: no `symbols`/`edges` tables, no Jedi, no
  agent, no HTTP endpoints.
- Pre-authorized new deps: `sentence-transformers`, `pgvector` (the
  Python client, for the asyncpg codec), `pyyaml`. Nothing else.
- CLAUDE.md rule 3 interpretation: `app/ingest/embedder.py` remains the
  **only** module importing anything from `sentence_transformers` —
  including the CrossEncoder. Expose `get_embedder()` and
  `get_reranker()` factory singletons from there; `retrieval/` imports
  those factories, never the library.
- The EVAL question set is **frozen**. Never edit questions or ground
  truth. Never tune retrieval against an individual question. If a
  §12 parameter must change, justify it generically, apply it, rerun
  the *full* eval, and log a DECISIONS entry.
- ruff: attempt once at the end; expected WDAC-blocked; note as
  deferred. mypy via the pure-Python build as before.
- Small logical commits.

## Reconciliations (do these, log each in DECISIONS.md)

1. **§5.2 without the symbols table.** SPEC §5.2 specifies lookups
   against `symbols(repo_id, name)`, which is a Phase 3 table. Chunks
   already carry full dotted qualnames, so implement identifier
   injection against `chunks.symbol` instead: a candidate identifier
   `name` matches where `symbol = name` OR `symbol LIKE '%.' || name`.
   Phase 3 may migrate this to the symbols table if it proves better.
2. **Real token counter.** Swap the ingestion pipeline's
   `HeuristicTokenCounter` for the embedder's real `token_len` (SPEC
   §4). Keep the heuristic implementation for unit tests so the test
   suite never downloads models. Update the Phase 1 DECISIONS entry
   with a superseding note. Expect the chunk count to drift from
   Phase 1's 1371 — report old vs new in the CLI stats.
3. **Eval path guard.** `truth.files` entries are repo-relative posix
   paths (e.g. `httpx/_config.py`). eval.py must warn loudly if any
   truth file is absent from the `files` table — that catches
   path-format drift before it silently zeroes a question.

## Deliverables

### 1. Migration `002_files_chunks.sql`
The `files` and `chunks` tables **exactly as written in SPEC §3**
(003 is Phase 3's). Apply via migrate.py.

### 2. `app/ingest/embedder.py` (SPEC §4)
- Implements the `Embedder` protocol: `dim`, batched `encode` with
  `normalize_embeddings=True`, `token_len` via the model's tokenizer.
- `get_embedder()` / `get_reranker()` module-level singletons, loaded
  once per process; model ids from config. Reranker constructed with
  `max_length=RERANK_PASSAGE_TOKENS` so truncation is handled by the
  CrossEncoder itself.

### 3. DB write path
- Register the pgvector codec on pool init (`pgvector.asyncpg
  register_vector`).
- `app/db/` query helpers for batched file and chunk inserts
  (executemany-style, not row-at-a-time).

### 4. CLI `--db` mode
`python -m app.ingest.cli <url> --db` performs, synchronously:
upsert repo row by url → **delete-and-replace** all existing
files/chunks for that repo (idempotency per §10's concept) → store
surviving files → chunk with the real token counter → embed in batches
with progress prints every `PROGRESS_EVERY_N` chunks → insert →
update repo counters and set status `ready`. Stats block gains: DB
timings, chunk-count delta vs the heuristic counter, embedding
throughput. (The full async job lifecycle stays in Phase 4 — this is
the CLI front-run of it.)

### 5. `app/retrieval/hybrid.py`
`hybrid_search(repo_id, query, k=SEARCH_K) -> list[SearchHit]` — the
single public entry point (CLAUDE.md rule 2):
1. In one transaction: `SET LOCAL hnsw.ef_search = 100;` then the §5.1
   RRF SQL verbatim (vector top-`VEC_K` ⊕ FTS top-`FTS_K`, `RRF_K`=60).
2. §5.2 injection per Reconciliation 1: extract identifier-like tokens
   from the query (the §5.2 regex — keep tokens with an underscore,
   CamelCase, or a dot), match against `chunks.symbol`, add ≤10 hits.
3. Dedupe; rerank the pool per §5.3; return top-k `SearchHit` dicts
   with the exact fields SPEC lists (preview ≤200 chars).

### 6. `scripts/debug_search.py`
`--repo <url|id> --query "..."` prints side-by-side per-signal tables:
vector top-N with distances, FTS top-N with ts_rank, fused RRF order,
injected identifiers and their matches, final reranked order with
cross-encoder scores — each row labeled `file_path :: symbol`. This is
the retrieval debugger; build it before running the eval.

### 7. `scripts/eval.py`
- Parse the YAML fence out of `docs/EVAL.md` (pyyaml).
- Modes: `vector` | `fts` | `hybrid` | `hybrid+rerank` (`--mode all`
  default). For the single-signal modes, run just that leg of the
  query; no reranking except in `hybrid+rerank`.
- Metrics per SPEC §11.2: hit@5 and hit@10. File match: chunk
  `file_path` ∈ truth.files (any-of, exact repo-relative). Symbol
  match: qualname equals the short name or ends with `"." + name`
  (the rule frozen in EVAL.md).
- Output: per-question hit/miss grid per mode, summary table, and a
  **dated results block appended** to EVAL.md. Old blocks are never
  edited. Include the truth-file guard from Reconciliation 3.

### 8. Tests
- Unit (no DB, no model downloads — heuristic counter, fake vectors):
  RRF fusion math on synthetic rank lists; the identifier-extraction
  regex; the symbol match rule; EVAL.md YAML-fence parsing.
- Integration, marked and cleanly skipped when the DB is unreachable:
  build a tiny throwaway git repo in tmp (`git init`, 3 small .py
  files), ingest with `--db` twice, assert counts stable and rows
  replaced (idempotency); a `hybrid_search` smoke test against it.
  Integration tests may load the real embedder.

## Verification — run and show output

```bash
cd backend
uv run pytest                      # unit + integration (DB up)
uv run mypy app
uv run python -m app.ingest.cli https://github.com/encode/httpx --db
uv run python scripts/eval.py --mode all
uv run python scripts/debug_search.py --repo <id> --query "<the worst-scoring question>"
uv run ruff check .                # expected blocked; note it
```

Paste the ingest stats (including the token-counter chunk delta) and
the full eval summary table.

**Done-when check:** `hybrid+rerank` hit@10 ≥ every single-signal mode.
If it isn't, do NOT wrap up: diagnose with debug_search.py, fix the
root cause (generic, never per-question), rerun the full eval, and
report what changed. If you cannot get there, stop and present the
diagnosis instead of papering over it.

## Wrap up

1. ROADMAP.md: Phase 2 → done, tick its checkboxes (chunk counts,
   eval numbers recorded).
2. DECISIONS.md: the three reconciliation entries.
3. Final commit. ≤10-line summary: what exists, the eval table, the
   chunk-count delta, anything flagged.