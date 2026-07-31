# CLAUDE.md — Codebase Onboarding Assistant

RAG + agent app. User submits a GitHub repo URL → background job clones it,
chunks code on AST boundaries via tree-sitter, embeds chunks, and builds a
symbol graph (definitions + import/call edges) in Postgres. User then asks
questions ("how does auth work?") → hybrid retrieval finds entry points →
a LangGraph agent traverses the symbol graph to pull in dependent code the
retriever missed → answers stream over SSE with file/line citations.

**v1 scope:** public GitHub repos, Python code only, single user, no auth.
Deferred to v2: TypeScript grammar, commit-history indexing, private repos.

## Source-of-truth docs

| File | What | Your obligation |
|---|---|---|
| `docs/SPEC.md` | Schema, chunk format, tool signatures, API contracts, retrieval algorithm | Read the relevant section before implementing it |
| `docs/ROADMAP.md` | Phases with "done when" criteria | Check current phase before starting; update status on completion |
| `docs/V2.md` | Multi-tenant roadmap (phases V1–V5), same format | v2 only — do not start any of it while a v1 phase is open |
| `docs/DECISIONS.md` | Append-only decision log | Read before proposing stack/architecture changes; append a dated entry when we make one |
| `docs/EVAL.md` | 20 frozen benchmark questions | Never tune retrieval against these by hand; run `scripts/eval.py` |
| `docs/prompts/` | Phase prompts, written just-in-time | Reference only |

If a referenced doc or section does not exist yet, say so and stop —
do not invent its contents.

## Repo layout

```
/
├── CLAUDE.md
├── docker-compose.yml        # postgres:16 (pgvector) + redis
├── docs/
├── backend/
│   ├── pyproject.toml        # managed with uv
│   ├── scripts/              # migrate.py, eval.py
│   └── app/
│       ├── main.py           # FastAPI entrypoint
│       ├── config.py         # pydantic-settings, all env access
│       ├── worker.py         # ARQ worker entrypoint
│       ├── db/               # asyncpg pool, queries, migrations/*.sql
│       ├── ingest/           # clone, filter, parse, chunk, embed
│       ├── retrieval/        # hybrid_search, rerank
│       ├── agent/            # LangGraph graph, tools, prompts
│       └── api/              # routes only; no business logic
└── frontend/                 # Next.js 15 App Router
```

## Stack — locked (see DECISIONS.md before questioning)

| Layer | Choice |
|---|---|
| Backend | Python 3.11+, FastAPI, fully async, Pydantic v2, uv; serving hardening (pool, inference limits, observability, per-IP rate limiting; 2026-07-28) |
| Parsing | tree-sitter (bindings ≥0.22, grammars as pip packages) + tree-sitter-python; Jedi for import resolution |
| Embeddings | sentence-transformers; model from env, default `BAAI/bge-small-en-v1.5` |
| Store | Postgres 16 + pgvector (HNSW, cosine) + tsvector FTS; RRF fusion in one SQL query |
| Rerank | `BAAI/bge-reranker-v2-m3` via CrossEncoder, CPU |
| Agent | LangGraph state machine; **provider-configurable via `AGENT_MODEL`** (Gemini / Claude / Vertex), built only by `app/agent/model.py` |
| Queue | ARQ on Redis |
| Transport | SSE via sse-starlette |
| Frontend | Next.js 15, TS strict, pnpm, Tailwind + shadcn/ui, custom `useRepoChat` over hand-rolled SSE (DECISIONS 2026-07-27; no Vercel AI SDK), Shiki (fine-grained, python-only), TanStack Query; markdown answers + citation viewer (2026-07-28) |
| Local dev | Docker Compose for pg + redis; apps run on host |

Agent tools (signatures in SPEC): `search_code`, `read_file`,
`get_definition`, `find_references`, `expand_context`, `list_directory`.

## Hard rules

1. **Never clone/parse/embed inside an HTTP handler.** API enqueues an ARQ
   job and returns; progress is written to Postgres and polled/streamed.
2. **All retrieval goes through `retrieval.hybrid_search()`** — the single
   RRF SQL query. No ad-hoc vector-only or FTS-only queries in features.
3. **Embeddings only via the `app/ingest/embedder.py` interface.** Nothing
   else imports sentence-transformers. Model name comes from config.
4. **Chunk boundaries come from tree-sitter AST nodes** (function / method /
   class). Oversized nodes split on statement boundaries. Never raw
   character splits.
5. **Every chunk row carries `repo_id`, `file_path`, `start_line`,
   `end_line`.** Citations are not optional; an answer without them is a bug.
6. **Agent loop is hard-capped at 8 tool executions**, then a forced final
   answer. All intermediate events stream to the client.
7. **SSE only.** Do not introduce WebSockets.
8. **Postgres and Redis are the only datastores.** Do not add Qdrant,
   Chroma, or any vector DB.
9. **Python-target repos only in v1.** No new tree-sitter grammars without
   a SPEC update and a DECISIONS entry.
10. **Ingestion filters live in `app/ingest/filters.py` only** (ignore
    dirs, size caps, binary detection). Numbers live in SPEC, not scattered.
11. **Ask before adding any dependency** to pyproject.toml or package.json.
12. **All config via env through `app/config.py`** (pydantic-settings).
    No `os.environ` elsewhere; no hardcoded secrets, models, or URLs.

## Conventions

**Backend.** Async end-to-end: asyncpg pool (no ORM), async ARQ tasks.
Services raise typed exceptions from `app/exceptions.py`; only the api
layer maps them to HTTP responses. Type hints everywhere; `mypy app/`
and `ruff check` must pass. Tests with pytest + pytest-asyncio; every
phase ships with tests, not after.

**Migrations.** Plain numbered SQL files in `backend/app/db/migrations/`
(`001_init.sql`, `002_....sql`) applied by `scripts/migrate.py`. Never
edit an applied migration; add a new one. No Alembic.

**Frontend.** Server components by default; `"use client"` only where
interaction requires it. Chat state via `useChat`; all other server state
via TanStack Query. No Redux/Zustand. Code rendering via Shiki with
line-range highlight support for citations.

## Commands

```bash
docker compose up -d                      # pg + redis
cd backend
uv sync
uv run python scripts/migrate.py
uv run uvicorn app.main:app --reload      # api :8000
uv run arq app.worker.WorkerSettings      # worker (separate terminal)
uv run pytest
uv run ruff check . && uv run mypy app
cd ../frontend
pnpm install && pnpm dev                  # :3000
```

## Environment

| Var | Purpose |
|---|---|
| `DATABASE_URL` | Postgres DSN |
| `REDIS_URL` | ARQ queue |
| `ANTHROPIC_API_KEY` | Agent model |
| `AGENT_MODEL` | Model id for the agent loop; prefix selects the provider (`gemini` / `claude` / `vertex:`) |
| `GOOGLE_API_KEY` | Gemini (AI Studio) — the default tuning provider |
| `GOOGLE_APPLICATION_CREDENTIALS` / `GCP_PROJECT` / `GCP_LOCATION` | Vertex — measurement runs and the cross-check only |
| `FRONTEND_ORIGIN` | CORS origin for the web app; default `http://localhost:3000` |
| `EMBEDDING_MODEL` | Default `BAAI/bge-small-en-v1.5` |
| `RERANKER_MODEL` | Default `BAAI/bge-reranker-v2-m3` |
| *serving limits* | Pool sizing, timeouts, rate limits, inference threads, logging, metrics — all defaulted; see `backend/.env.example` and DECISIONS 2026-07-28 |

`.env` at `backend/.env`, loaded by config.py. Commit `.env.example`, never `.env`.

**Database note:** Neon holds the ingested benchmark corpus (`encode/httpx`
@ `b5addb64`, 1522 chunks: 825 impl / 697 test). Re-ingest only if schema or
chunker changes. Verify with:
```sql
SELECT count(*) FILTER (WHERE NOT c.is_test) AS impl,
       count(*) FILTER (WHERE c.is_test)     AS test
  FROM chunks c
  JOIN repo_snapshots sn ON sn.id = c.snapshot_id
  JOIN repo_sources   s  ON s.id  = sn.source_id
 WHERE s.url = 'https://github.com/encode/httpx'
   AND sn.strategy = 'ast';
```
Should return `825 | 697`.

**The scoping is load-bearing** — do not shorten this back to a bare
`FROM chunks`. That was the original wording and it stopped being correct the
moment a second repo was ingested: the database now holds seven sources and two
httpx corpora (`ast` and the §2.7 `naive` baseline, at the *same* commit), so
unscoped it returns `1555 | 1170` and reads as a corrupted benchmark. Both the
source and the strategy are needed; either one alone still counts the wrong
rows (corrected 2026-07-31).

## Phase status (2026-07-28)

**Phase 6 — Evidence & ship: nearly complete.** Only `demo.gif` remains
(human task — record a 20s clip: submit repo → progress → question → tool
timeline streams → answer with citations → click citation → viewer highlights).
ROADMAP marked done-when satisfied: README table filled from measured eval,
stranger re-run passed, findings (a) corrected to MODEL-DEPENDENT.

**Recent work (2026-07-28):** Landing page visual pass (design tokens, typeface,
hero), chat page polish (markdown answers, following code pane, stop/clear),
backend serving hardening (pool, inference limits, observability, per-IP
rate limiting).

**Phase 3 findings (exact wording for README/docs):**
- **(a) MODEL-DEPENDENT** — q10 answered 3/3 controlled temp-0 Mistral, 0/3
  Vertex; graph traversal can reach what retrieval cannot, demonstrated on
  one model family and not reproduced on the other.
- **(b) MODERATE, directionally stable** — agent leads at symbol level in
  6/6 runs (Mistral +5/+4/+2 vs 0.75, Vertex +1/+1/+2 vs 0.80); sign stable,
  magnitude noisy.
- **(c) NOT SUPPORTED** — graph-tool use does not predict correctness; perfect
  model inversion at temperature 0.

**Naive baseline result:** Fixed 1000-char windows tie AST at hit@10 0.95;
case for AST rests on symbol graph and answer-level numbers, not retrieval
hit-rate.

## Working agreement

- Before any task: check the current phase in ROADMAP.md and work only
  toward its "done when" criteria. Flag scope creep instead of building it.
- Done means: code + tests passing + lint/type clean + ROADMAP status
  updated + DECISIONS entry if an architectural choice was made.
- When retrieval quality is in question, run `scripts/eval.py` and report
  numbers. Do not eyeball.
- Prefer boring, readable code over clever abstractions. This is a
  portfolio project; a reviewer should understand any file in one read.
