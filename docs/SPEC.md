# SPEC.md — Codebase Onboarding Assistant

Deep technical spec. Read the section for the phase you are implementing
(mapping in ROADMAP.md). Constants live in §12 and nowhere else. If code
and SPEC disagree, stop and reconcile — update one or the other, and log
it in DECISIONS.md.

## §1 System overview

```
Next.js ──POST/SSE──> FastAPI ──> LangGraph agent
                        │              ├─> retrieval.hybrid_search()
                        │              └─> symbol-graph tools
                        │
                        ├─> Postgres (repos, files, chunks, symbols, edges)
                        └─> Redis ──> ARQ worker (clone → parse → embed)
```

Two processes share one codebase: the API (uvicorn) and the worker (arq).
Both read config from `app/config.py`. The clone directory is ephemeral —
after ingestion, everything the system needs (including raw file contents)
lives in Postgres.

Non-goals in v1: TypeScript, commit history, private repos, auth,
multi-user, incremental re-indexing.

## §2 Ingestion pipeline

### 2.1 Clone
- `git clone --depth 1 --single-branch <url> <workdir>/<repo_id>` via
  GitPython. Depth 1 because commit history is out of scope for v1.
- Record `head_sha` and default branch on the repo row.
- Workdir is deleted on completion *and* on failure (try/finally).

### 2.2 File selection (`app/ingest/filters.py`)
1. Candidate set = `git ls-files` output (tracked files only — this
   inherits `.gitignore` for free; do not reimplement gitignore logic).
2. Keep only `*.py` (v1).
3. Drop files under `IGNORE_DIRS` (§12) at any path depth.
4. Drop files larger than `MAX_FILE_BYTES`.
5. Drop files containing a null byte in the first 8 KB (binary sniff).
6. Drop files that fail UTF-8 decode — log a warning, do not crash.
7. Abort ingestion with a clear error if survivors exceed `MAX_FILES`.

Store every surviving file's full text in the `files` table (§3). This is
what `read_file`, `list_directory`, and the frontend viewer serve from —
the on-disk clone is never needed after ingestion.

### 2.3 Parsing (tree-sitter)
Bindings ≥0.22: grammars come as pip packages
(`tree_sitter_python.language()`), no `.so` compilation.

Extract, per file:
- **module chunk** (0 or 1): module docstring + import block + top-level
  assignments not inside any def/class. Skip if trivially empty.
- **class skeleton chunk** per class: `class` line, docstring, class-level
  attributes, and the *signatures* of its methods (bodies elided). Gives
  the overview without duplicating method bodies.
- **function/method chunk** per def: full body, including decorators and
  the docstring. Nested defs stay inside their parent's chunk (no separate
  chunk below depth 1). Async and decorated defs are handled by the query.

### 2.4 Chunk text format
The embedded text is `header + "\n---\n" + code`:

```
# File: src/auth/middleware.py
# Symbol: src.auth.middleware.AuthMiddleware.verify_token
# Kind: method
# Signature: def verify_token(self, token: str) -> User | None
# Imports: jwt; from .models import User; from .config import SECRET
---
<decorators + def line + docstring + body>
```

- `Symbol:` is the **full dotted qualname** (§3), never class-relative:
  `<module path>.<Class>.<method>`. The module path derives from the
  repo-relative file path — `a/b/c.py` → `a.b.c`, `a/b/__init__.py` →
  `a.b` — so a method of `AuthMiddleware` in `src/auth/middleware.py` is
  `src.auth.middleware.AuthMiddleware.verify_token`. Chunk `symbol`
  fields and header `Symbol:` lines both use this form.
- `Imports:` lists the *file-level* import statements (semicolon-joined),
  because they need no resolution. Per-symbol caller info ("Called by:")
  is **not** embedded — it is attached at context-assembly time in §7.4,
  once the symbol graph exists. Rationale: keeps embeddings stable when
  the graph changes; no re-embedding pass.
- Module chunks: `Symbol` is the module path itself, `Kind: module`, no
  signature line.

### 2.5 Oversize handling
If `embedder.token_len(text) > CHUNK_TOKEN_MAX`, split the body on
top-level statement boundaries into parts. Each part repeats the full
header with `# Part: 2/3` appended. Never split mid-statement, never
split on raw character counts.

## §3 Database schema

Migrations are plain SQL in `backend/app/db/migrations/`:
`001_init.sql` (repos) → `002_files_chunks.sql` → `003_symbols.sql`.

```sql
-- 001
CREATE TABLE repos (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  url           TEXT NOT NULL UNIQUE,
  name          TEXT NOT NULL,                -- "owner/repo"
  default_branch TEXT,
  head_sha      TEXT,
  status        TEXT NOT NULL DEFAULT 'queued',
    -- queued | cloning | parsing | embedding | ready | failed
  error         TEXT,
  files_total   INT NOT NULL DEFAULT 0,
  files_parsed  INT NOT NULL DEFAULT 0,
  chunks_total  INT NOT NULL DEFAULT 0,
  chunks_embedded INT NOT NULL DEFAULT 0,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 002
CREATE TABLE files (
  id       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  repo_id  UUID NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
  path     TEXT NOT NULL,
  content  TEXT NOT NULL,
  n_lines  INT  NOT NULL,
  UNIQUE (repo_id, path)
);

CREATE TABLE chunks (
  id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  repo_id    UUID NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
  file_path  TEXT NOT NULL,
  symbol     TEXT,            -- qualified name; module path for module chunks
  kind       TEXT NOT NULL,   -- function | method | class | module
  part       INT NOT NULL DEFAULT 1,
  n_parts    INT NOT NULL DEFAULT 1,
  start_line INT NOT NULL,
  end_line   INT NOT NULL,
  header     TEXT NOT NULL,
  code       TEXT NOT NULL,
  embedding  vector(384) NOT NULL,   -- dim tied to EMBEDDING_MODEL; changing
                                     -- models = new migration + full re-embed
  tsv tsvector GENERATED ALWAYS AS
      (to_tsvector('english', header || ' ' || code)) STORED
);
CREATE INDEX chunks_hnsw ON chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX chunks_tsv  ON chunks USING gin (tsv);
CREATE INDEX chunks_repo_file ON chunks (repo_id, file_path);

-- 003
CREATE TABLE symbols (
  id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  repo_id    UUID NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
  name       TEXT NOT NULL,        -- short name
  qualname   TEXT NOT NULL,        -- pkg.module.Class.method
  kind       TEXT NOT NULL,        -- function | method | class | module
  file_path  TEXT NOT NULL,
  start_line INT NOT NULL,
  end_line   INT NOT NULL,
  UNIQUE (repo_id, qualname, file_path, start_line)
);
CREATE INDEX symbols_name ON symbols (repo_id, name);

CREATE TABLE edges (
  id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  repo_id     UUID NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
  from_symbol BIGINT NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
  to_symbol   BIGINT NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
  kind        TEXT NOT NULL,       -- imports | calls | extends
  line        INT,                 -- site line in from_symbol's file
  UNIQUE (from_symbol, to_symbol, kind, line)
);
CREATE INDEX edges_from ON edges (from_symbol);
CREATE INDEX edges_to   ON edges (to_symbol);

ALTER TABLE chunks ADD COLUMN symbol_id BIGINT REFERENCES symbols(id);
-- backfilled during the symbol pass
```

Postgres FTS note: the default parser splits `verify_token` into `verify`
and `token` — desirable for code search (querying "token" finds it).
Exact-identifier lookup is handled separately in §5.2.

## §4 Embedding

`app/ingest/embedder.py` is the only module that imports
sentence-transformers.

```python
class Embedder(Protocol):
    dim: int
    def encode(self, texts: list[str], batch_size: int = 64) -> list[list[float]]: ...
    def token_len(self, text: str) -> int: ...
```

- `normalize_embeddings=True` (we use cosine distance).
- Loaded once per process at startup (worker and eval script). Never
  lazily inside a request.
- Model id from `EMBEDDING_MODEL`; default `BAAI/bge-small-en-v1.5`
  (384-dim, 512-token window — hence `CHUNK_TOKEN_MAX` in §12).

## §5 Retrieval

### 5.1 Fusion query (one SQL statement)
```sql
WITH vec AS (
  SELECT id, ROW_NUMBER() OVER (ORDER BY embedding <=> $1) AS rnk
  FROM chunks WHERE repo_id = $2
  ORDER BY embedding <=> $1 LIMIT 40
),
fts AS (
  SELECT id, ROW_NUMBER() OVER (ORDER BY ts_rank(tsv, q) DESC) AS rnk
  FROM chunks,
       to_tsquery('english',
         replace(plainto_tsquery('english', $3)::text, ' & ', ' | ')) AS q
  WHERE repo_id = $2 AND tsv @@ q
  ORDER BY ts_rank(tsv, q) DESC LIMIT 40
)
SELECT COALESCE(v.id, f.id) AS chunk_id,
       COALESCE(1.0/(60 + v.rnk), 0) + COALESCE(1.0/(60 + f.rnk), 0) AS rrf
FROM vec v FULL OUTER JOIN fts f ON v.id = f.id
ORDER BY rrf DESC
LIMIT 40;
```
Set `SET LOCAL hnsw.ef_search = 100;` before the query — HNSW applies the
`repo_id` filter post-scan, and the default ef_search starves filtered
results on multi-repo databases.

**FTS tsquery construction (OR, not AND).** The FTS leg **OR-combines** the
query's content lexemes rather than ANDing them. `plainto_tsquery('english', …)`
already strips english stopwords and stems; we take its output and swap `&` for
`|` (`replace(…::text, ' & ', ' | ')`, re-parsed with `to_tsquery`). A bare
`plainto_tsquery` ANDs every term, which is unsatisfiable for a full
natural-language question over code (no single chunk contains all of
`request & timeout & class & defined`), so the FTS CTE returned zero rows and
RRF silently degenerated to vector-only. OR-combining makes FTS a lexical
**recall** signal; exact-symbol matching stays with §5.2 injection, not FTS. An
all-stopword query yields an empty tsquery that matches nothing (no error).
(Superseded `plainto_tsquery`; see DECISIONS 2026-07-25 "FTS leg is dead".)

### 5.2 Exact-symbol injection
Extract identifier-like tokens from the query
(`[A-Za-z_][A-Za-z0-9_]*`, keeping those with an underscore, CamelCase,
or a dot). Look them up in `symbols(repo_id, name)`; add the chunks of
any hits (≤10) to the candidate pool. This catches "where is
verify_token defined" even when FTS stemming mangles it.

### 5.3 Rerank
Candidates = fusion top-40 ∪ symbol-injected, deduped. Score each
(query, header + code truncated to `RERANK_PASSAGE_TOKENS`) pair with the
CrossEncoder (`RERANKER_MODEL`); return top-`SEARCH_K` ordered hits.

`retrieval.hybrid_search(repo_id, query, k=SEARCH_K)` is the single
public entry point (CLAUDE.md hard rule 2) and returns:

```python
SearchHit = {
  "chunk_id": int, "file_path": str, "start_line": int, "end_line": int,
  "symbol": str | None, "kind": str, "score": float, "preview": str  # ≤200 chars
}
```

## §6 Symbol graph

### 6.1 Symbol pass (Phase 3, runs after parsing within the same job)
- Definitions come from the tree-sitter pass (same nodes as chunking);
  insert into `symbols`, backfill `chunks.symbol_id`.
- Edges:
  - `imports`: for each import statement, resolve the target with Jedi
    (`jedi.Script(...).goto(line, col)` under a `jedi.Project(workdir)`);
    map the resolved path+line onto a symbol row. Targets outside the
    repo (stdlib, site-packages) are dropped.
  - `calls`: for each call expression inside a def (call sites located
    via tree-sitter), Jedi-resolve the callee; edge from enclosing symbol
    to callee symbol.
  - `extends`: class bases, resolved the same way.
- Budget: per-file resolution timeout `JEDI_FILE_TIMEOUT_S`; on timeout,
  log and skip the file's edges. Track and store the unresolved-edge rate
  on the repo row's error-free log output — ~20% unresolved is expected
  and acceptable.

### 6.2 Traversal semantics
`out` = edges where symbol is `from_symbol` (its callees/imports/bases);
`in` = edges where symbol is `to_symbol` (its callers/importers/subclasses).

## §7 Agent

### 7.1 Tools — exact signatures
All tools return JSON-serializable dicts. Failures return
`{"error": "<message>"}` instead of raising — the model should read the
error and adapt.

```python
search_code(query: str, k: int = 10) -> {"hits": [SearchHit]}

read_file(path: str, start_line: int | None = None,
          end_line: int | None = None) -> \
    {"path": str, "start_line": int, "end_line": int, "content": str}
    # content is line-numbered. Whole file only if n_lines ≤ READ_MAX_LINES;
    # otherwise an error instructs the model to pass a range.

get_definition(symbol: str) -> {"matches": [
    {"qualname": str, "kind": str, "file_path": str,
     "start_line": int, "end_line": int, "code": str}]}
    # matches on name or qualname suffix; ≤5 matches, code included

find_references(symbol: str, kind: str | None = None) -> {"references": [
    {"from_qualname": str, "file_path": str, "line": int, "kind": str}]}
    # incoming edges; kind filters imports|calls|extends

expand_context(symbol: str, depth: int = 1, direction: str = "out") -> {
    "edges": [{"from": str, "to": str, "kind": str}],
    "symbols": [{"qualname": str, "file_path": str, "start_line": int,
                 "end_line": int, "code": str}]}
    # BFS over edges, depth clamped to EXPAND_MAX_DEPTH, direction
    # out|in|both; total returned code capped at EXPAND_TOKEN_BUDGET,
    # breadth-first truncation with a "truncated": true flag

list_directory(path: str = "") -> {"tree": str}
    # 2-level tree with file sizes, from the files table
```

### 7.2 State and loop (LangGraph)
```python
class AgentState(TypedDict):
    repo_id: str
    question: str
    messages: list          # provider-format, incl. tool results
    tool_calls_used: int
    citations: list[dict]   # accumulated from tool results
```
Graph: `model` → (has tool calls AND used < AGENT_TOOL_CAP) → `tools` →
`model`; otherwise END. On hitting the cap, append a user-role message —
"Tool limit reached; answer now from what you have." — and make one final
model call with tools disabled. Stream via `astream_events(version="v2")`.

### 7.3 System prompt (outline — full text lives in `app/agent/prompts.py`)
- Role: senior engineer explaining THIS repo (name, file count, top-level
  dirs injected).
- Strategy: search first for entry points; follow imports/calls with
  `expand_context` / `get_definition` rather than re-searching; use
  `read_file` for precision once located.
- Citation contract: every factual claim about the code cites
  `[path/to/file.py:12-48]` inline; uncited claims about the code are
  forbidden; say so plainly if something can't be found.

### 7.4 Context assembly
Whenever a chunk/symbol body is placed into the model context (tool
results), append its incoming edges as a trailing comment block:
`# Called by: api/routes.py:34 (handle_login), api/routes.py:78 (refresh)`.
This is the "Called by" data deferred out of embeddings in §2.4.

### 7.5 Citations
The server regex-parses `[<path>:<start>-<end>]` markers from the final
answer, validates paths against `files`, and emits a `citations` event
(§9). Invalid paths are dropped from the event but left in the text.

## §8 HTTP API

FastAPI, JSON, Pydantic v2 models in `app/api/schemas.py`.

```
POST /repos            {"url": "https://github.com/owner/repo"}
  201 RepoOut (created, job enqueued) | 200 RepoOut (URL already known)
  422 invalid/non-GitHub URL
GET  /repos            {"repos": [RepoOut]}
GET  /repos/{id}       RepoOut | 404
GET  /repos/{id}/files?path=src/auth.py
  {"path": str, "content": str, "n_lines": int} | 404
  (powers the frontend code viewer and citation clicks)
POST /repos/{id}/chat  {"question": str}
  → text/event-stream (§9)
  409 {"detail": "repo not ready", "status": "<current>"} if not ready
  404 unknown repo
GET  /health           {"ok": true}
```

```python
RepoOut = {
  "id": UUID, "url": str, "name": str, "status": str, "error": str | None,
  "head_sha": str | None,
  "progress": {"files_total": int, "files_parsed": int,
               "chunks_total": int, "chunks_embedded": int},
  "created_at": datetime
}
```

Chat is POST (not GET/EventSource): the frontend consumes the stream with
fetch + ReadableStream via the AI SDK, and questions don't belong in URLs.
sse-starlette formats the response; the event schema is ours (§9).

## §9 SSE event schema

Events in order; `text` and tool events interleave.

```
event: status       data: {"state": "thinking"}
event: tool_call    data: {"n": 1, "tool": "search_code",
                           "args": {"query": "auth middleware"}}
event: tool_result  data: {"n": 1, "tool": "search_code",
                           "summary": "6 hits",
                           "locations": [{"file_path": "...",
                                          "start_line": 1, "end_line": 9}]}
event: text         data: {"delta": "Auth starts in "}
event: citations    data: {"citations": [{"file_path": "...",
                                          "start_line": 12, "end_line": 48}]}
event: done         data: {"tool_calls_used": 5}
event: error        data: {"message": "..."}
```

`tool_result` payloads carry locations and summaries only — never full
code bodies over the wire. The frontend renders steps from these and
fetches code via `/files` on demand.

## §10 Ingestion job lifecycle

```
queued → cloning → parsing → embedding → ready
   └────────┴─────────┴──────────┴────→ failed (error recorded)
```

- The ARQ task starts by deleting existing `files/chunks/symbols/edges`
  rows for the repo (delete-and-replace idempotency), then walks the
  states, updating counters and `updated_at` as it goes.
- `job_timeout = 900s`, `max_tries = 2`. A retry re-enters cleanly
  because of the delete-and-replace start.
- Zombie sweep: on worker startup, any repo in an in-flight state with
  `updated_at` older than `ZOMBIE_AFTER_S` → `failed("worker died")`.
- Progress writes are batched (every `PROGRESS_EVERY_N` files/chunks),
  not per-row.

## §11 Evaluation

### 11.1 Ground truth (`docs/EVAL.md`)
Benchmark repo pinned by name + commit SHA. 20 questions:

```yaml
- id: q01
  question: "Where are request timeouts enforced?"
  truth:
    files: ["httpx/_config.py"]        # ≥1 required
    symbols: ["Timeout"]               # optional
```

### 11.2 Metrics (`scripts/eval.py`)
- **hit@k** (k=5,10): a question scores 1 if any top-k SearchHit has
  `file_path ∈ truth.files` or `symbol ∈ truth.symbols`.
- **answer-hit**: the final answer contains ≥1 parsed citation whose
  file is in `truth.files`. Automatic — no human scoring in the loop.
- Modes: `vector` | `fts` | `hybrid` | `hybrid+rerank` (retrieval
  metrics); `stuffed` (one model call, top-10 chunks, no tools) vs
  `agent` (full loop) for answer-hit.
- Output: a dated results table appended to EVAL.md. Never edit old
  result blocks.

## §12 Constants (single source of truth)

| Name | Value | Used in |
|---|---|---|
| `IGNORE_DIRS` | `{".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache"}` | §2.2 |
| `MAX_FILE_BYTES` | 500_000 | §2.2 |
| `MAX_FILES` | 10_000 | §2.2 |
| `CHUNK_TOKEN_MAX` | 480 | §2.5 |
| `VEC_K` / `FTS_K` | 40 / 40 | §5.1 |
| `RRF_K` | 60 | §5.1 |
| `SEARCH_K` | 10 | §5.3 |
| `RERANK_PASSAGE_TOKENS` | 512 | §5.3 |
| `READ_MAX_LINES` | 400 | §7.1 |
| `EXPAND_MAX_DEPTH` | 2 | §7.1 |
| `EXPAND_TOKEN_BUDGET` | 6_000 | §7.1 |
| `AGENT_TOOL_CAP` | 8 | §7.2 |
| `JEDI_FILE_TIMEOUT_S` | 10 | §6.1 |
| `ZOMBIE_AFTER_S` | 1_200 | §10 |
| `PROGRESS_EVERY_N` | 25 | §10 |

Constants live in `app/config.py` under these exact names so SPEC and
code stay greppable against each other.
