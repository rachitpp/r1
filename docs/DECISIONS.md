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
