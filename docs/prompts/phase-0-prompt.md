# Phase 0 prompt — Foundations

> **How to use:** place `CLAUDE.md` at the repo root and `ROADMAP.md` +
> `SPEC.md` under `docs/` first. Then open Claude Code at the repo root
> and paste everything below the line.

---

You are starting **Phase 0 — Foundations** of this project.

## Step 0 — Orient

Read, in this order:
1. `CLAUDE.md` (all of it)
2. `docs/ROADMAP.md` — the Phase 0 section only
3. `docs/SPEC.md` — §1, §3 (the 001 migration only), §12

Confirm all three files exist. If any is missing, stop and tell me —
do not reconstruct their contents.

Then give me a plan of ≤10 lines and proceed. Only pause for questions
if something is genuinely ambiguous after reading the docs.

## Session rules

- Build **only Phase 0**. No parsing, no embeddings, no retrieval, no
  agent code, no real endpoints beyond `/health` — even if trivial.
- The dependency lists below are **pre-authorized**. Anything beyond
  them: ask first (CLAUDE.md rule 11).
- If this prompt conflicts with SPEC or ROADMAP, stop and flag it
  rather than picking silently.
- `git init` if needed. Commit in small logical units with clear
  messages.

## Deliverables

### 1. Repo hygiene
- `.gitignore`: Python (`.venv/`, `__pycache__/`, `.pytest_cache/`,
  `.mypy_cache/`, `.ruff_cache/`), Node (`node_modules/`, `.next/`),
  env files (`.env` — but **not** `.env.example`), OS junk (`.DS_Store`).
- `docs/DECISIONS.md` — create with the seed content in **Appendix A**,
  verbatim.
- `docs/EVAL.md` — create with the stub in **Appendix B**, verbatim.
- `docs/prompts/phase-0.md` — save this entire prompt, verbatim.

### 2. `docker-compose.yml` (repo root)
- `postgres`: image **`pgvector/pgvector:pg16`** — not plain
  `postgres:16`, which lacks the extension. Port 5432. Env:
  `POSTGRES_USER=app`, `POSTGRES_PASSWORD=app`,
  `POSTGRES_DB=codebase_assistant`. Named volume for data.
  Healthcheck: `pg_isready -U app -d codebase_assistant`.
- `redis`: `redis:7-alpine`, port 6379, healthcheck `redis-cli ping`.

### 3. Backend (`backend/`, managed with uv)
- `pyproject.toml` via uv, Python ≥3.11.
  - Runtime deps: `fastapi`, `uvicorn[standard]`, `pydantic-settings`,
    `asyncpg`, `arq`
  - Dev deps: `ruff`, `mypy`, `pytest`, `pytest-asyncio`, `httpx`
- `app/config.py`: a pydantic-settings `Settings` class loading
  `backend/.env`. Fields: `DATABASE_URL`, `REDIS_URL` (both required);
  `ANTHROPIC_API_KEY`, `AGENT_MODEL`, `EMBEDDING_MODEL`
  (default `BAAI/bge-small-en-v1.5`), `RERANKER_MODEL`
  (default `BAAI/bge-reranker-v2-m3`) — these four optional/defaulted so
  Phase 0 runs without model keys. Also define **every constant from
  SPEC §12, with those exact names**, in this module.
- `app/main.py`: FastAPI app. `GET /health` → `{"ok": true}`. Nothing
  else. Lifespan creates/closes an asyncpg pool via `app/db/pool.py`,
  but tolerates connection failure with a logged warning — `/health`
  must not require any service.
- `app/db/pool.py`: asyncpg pool create/close helpers.
- `app/worker.py`: ARQ `WorkerSettings` reading Redis settings from
  config, with one no-op task `ping` returning `"pong"`, and an
  `on_startup` that logs a one-line config summary.
- `app/db/migrations/001_init.sql`: `CREATE EXTENSION IF NOT EXISTS
  vector;` followed by the `repos` table **exactly as written in SPEC
  §3** (002 and 003 come in later phases).
- `scripts/migrate.py`:
  - Ensures `schema_migrations(version INT PRIMARY KEY, applied_at
    TIMESTAMPTZ NOT NULL DEFAULT now())` exists.
  - Scans the migrations dir for `NNN_*.sql`, applies unapplied files in
    numeric order, each in a transaction, recording the version.
  - Prints applied vs skipped; non-zero exit on failure. Running it
    twice in a row must be a no-op.
- `backend/.env.example` with all vars above, dev values matching
  compose (`DATABASE_URL=postgresql://app:app@localhost:5432/codebase_assistant`,
  `REDIS_URL=redis://localhost:6379`, `AGENT_MODEL=claude-sonnet-4-6`
  with a comment to update to the current model id). Also copy it to
  `backend/.env` locally so things run — it is gitignored.
- `tests/test_health.py`: async test (httpx `ASGITransport`) asserting
  `GET /health` → 200 `{"ok": true}` **with no services running**.
- Tooling config in `pyproject.toml`: ruff line-length 100 (lint +
  format); mypy scoped to `app/` with per-module
  `ignore_missing_imports` only where stubs are missing (asyncpg, arq);
  pytest with `asyncio_mode = "auto"`.

### 4. Frontend (`frontend/`)
- Next.js 15, App Router, TypeScript strict, pnpm, Tailwind.
- shadcn/ui init with defaults; add **only** the Button component as a
  smoke test.
- One page at `/` rendering the project name and that Button. No other
  routes, no other libraries — TanStack Query and the AI SDK arrive in
  Phase 5.

## Verification — run these and show me the output

```bash
docker compose up -d && docker compose ps          # both healthy
cd backend && uv sync
uv run python scripts/migrate.py                   # applies 001
uv run python scripts/migrate.py                   # no-op (idempotent)
uv run uvicorn app.main:app --port 8000 &          # then:
curl -s localhost:8000/health                      # {"ok": true}
uv run arq app.worker.WorkerSettings               # starts, connects; then stop it
uv run pytest
uv run ruff check .
uv run mypy app
cd ../frontend && pnpm install && pnpm build       # compiles clean
```

Fix anything red before wrapping up. If something can't go green,
say why instead of papering over it.

## Wrap up

1. `docs/ROADMAP.md`: set Phase 0 status to `done` and tick its
   done-when checkboxes.
2. Final commit.
3. Give me a ≤10-line summary: what exists, how to run it, anything
   you flagged.

---

## Appendix A — `docs/DECISIONS.md` seed (create verbatim)

```markdown
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
```

## Appendix B — `docs/EVAL.md` stub (create verbatim)

```markdown
# EVAL.md — frozen benchmark

**STATUS: NOT YET WRITTEN.** To be authored blind in Phase 1 — after
chunking works, before ANY retrieval code exists (ROADMAP Phase 1).

Benchmark repo: **TBD** (candidates: encode/httpx, pallets/flask).
Pin `owner/name` + commit SHA here when chosen.

Question format (SPEC §11.1):

    - id: q01
      question: "Where are request timeouts enforced?"
      truth:
        files: ["httpx/_config.py"]
        symbols: ["Timeout"]

Rules: exactly 20 questions; ground truth is file paths (symbols
optional); questions are frozen once Phase 2 begins; `scripts/eval.py`
appends dated result blocks below and old blocks are never edited.

## Results

(appended by scripts/eval.py)
```
