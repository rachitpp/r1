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

### 2.6 Test classification (`is_test`)
Every chunk carries `is_test`, computed from its `file_path` by
`filters.is_test_path()` at ingest. The rule is corpus-wide and purely
positional — no per-file judgment, no content inspection:

- any **directory** segment (at any depth) in `TEST_DIR_SEGMENTS` (§12), or
- the filename is `test_*.py`, `*_test.py`, or in `TEST_FILE_NAMES` (§12).

Only directory segments count for the first rule, so `httpx/test.py` is *not*
test code while `httpx/tests/thing.py` is. Substrings never match: `protest.py`
and `contest.py` are implementation.

**This classifies; it does not exclude.** Selection (§2.2) is unchanged, test
files are still stored in `files`, and `read_file` / `list_directory` still see
them. Only retrieval filters on the flag (§5.4).

### 2.7 Naive chunking — measurement baseline only (`app/ingest/naive.py`)
A second chunk strategy exists so the README can state what AST chunking buys,
measured rather than asserted. It splits each file into fixed character windows
of `NAIVE_CHUNK_CHARS` advancing by `NAIVE_CHUNK_CHARS - NAIVE_CHUNK_OVERLAP_CHARS`
(§12) — no tree-sitter, no Jedi, no symbol awareness.

**This is a scoped exception to the AST-boundaries rule, not a softening of
it.** The exception is bounded by construction:

- reachable only via `python -m app.ingest.cli <url> --db --strategy naive`;
  `POST /repos` and the ARQ worker have no path to it
- the baseline corpus lives in its own `repos` row, keyed `<url>#naive` with
  name `<name>@naive`. `repos.url` is UNIQUE (§3), so a distinct key is what
  lets the two corpora coexist; the fragment is stripped before cloning and is
  never handed to git
- `build_graph` is forced off — the symbol graph is an AST product, so a
  "naive + graph" corpus would not be a baseline of anything
- **citations still hold.** Every window carries `file_path`, `start_line`,
  `end_line` (§3). The rule-5 contract is outside the carve-out.

The header (§2.4) keeps its shape, but `Symbol`, `Kind`, and `Imports` are
AST products and are left empty or synthetic (`<path>:w<i>`, `window`). The
comparison therefore measures *AST chunking plus its enrichment* against
*fixed windows* — the whole strategy, not boundaries in isolation. Holding the
header constant to isolate boundaries alone is a v2 ablation.

## §3 Database schema

Migrations are plain SQL in `backend/app/db/migrations/`:
`001_init.sql` (repos) → `002_files_chunks.sql` → `003_is_test.sql` →
`004_symbols.sql` (Phase 3).

```sql
-- 001
CREATE TABLE repos (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  url           TEXT NOT NULL UNIQUE,
  name          TEXT NOT NULL,                -- "owner/repo"
  default_branch TEXT,
  head_sha      TEXT,
  status        TEXT NOT NULL DEFAULT 'queued',
    -- queued | cloning | parsing | linking | embedding | ready | failed
    -- (`linking` added in Phase 4; see §10. No migration: the column is TEXT.)
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
-- Test chunks stay in the corpus but are flagged, so retrieval can target
-- implementation by default (§2.6 classification, §5.4 filter).
ALTER TABLE chunks ADD COLUMN is_test BOOLEAN NOT NULL DEFAULT FALSE;

-- 004
CREATE TABLE symbols (
  id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  repo_id    UUID NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
  name       TEXT NOT NULL,        -- short name
  qualname   TEXT NOT NULL,        -- pkg.module.Class.method
  kind       TEXT NOT NULL,        -- function | method | class | module
  file_path  TEXT NOT NULL,
  start_line INT NOT NULL,
  end_line   INT NOT NULL,
  is_test    BOOLEAN NOT NULL DEFAULT FALSE,  -- from the file's §2.6 rule
  UNIQUE (repo_id, qualname, file_path, start_line)
);
CREATE INDEX symbols_name ON symbols (repo_id, name);
CREATE INDEX symbols_repo_is_test ON symbols (repo_id, is_test);

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
CREATE INDEX chunks_symbol_id ON chunks (symbol_id);
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
verify_token defined" even when FTS stemming mangles it. Injection
respects the §5.4 test filter.

**CamelCase test.** A token counts as CamelCase only if it has an uppercase
letter at a **non-initial** position *and* at least one lowercase letter.
Without the non-initial requirement, an ordinary capitalised first word is read
as an identifier — injection was extracting `How` from "How does httpx…" and
`When` from "When I pass auth…" (observed 2026-07-26). Consequences of the rule,
accepted deliberately:

| Token | Kept | Why |
|---|---|---|
| `BasicAuth`, `URLPattern`, `TextDecoder` | yes | internal capital + lowercase |
| `How`, `When`, `Where` | no | only capital is initial |
| `URL`, `HTTP` | no | no lowercase letter |
| `Timeout`, `Response` | no | indistinguishable from a sentence-initial capital |

The last row is a real cost: single-capital class names lose §5.2 injection. It
is accepted because the rule cannot separate them from sentence-initial words
without a symbol-table lookup, and because the vector and FTS legs still reach
those symbols — injection is an *extra* signal, not the only one.

### 5.4 Corpus condition (test filtering)
`hybrid_search(..., include_tests=False)` is the default and the production
behaviour: chunks with `is_test` (§2.6) are excluded from **both fusion CTEs**
and from §5.2 injection. Filtering happens inside each CTE, *before* the per-leg
`LIMIT`, so a test chunk never consumes a top-`VEC_K`/`FTS_K` slot that
implementation could have taken.

Rationale: test files are written in user vocabulary while implementation is
terse, so test chunks systematically outrank implementation for
natural-language questions — in the lexical leg and in the cross-encoder alike
(DECISIONS 2026-07-26). For an onboarding assistant, "how does X work" should
answer with the implementation.

`include_tests=True` restores the shadowed condition. It exists so the
counterfactual stays measurable (`scripts/eval.py --both-conditions`) rather
than merely asserted. The `files` table is untouched — `read_file` and
`list_directory` still serve test files.

### 5.3 Rerank — optional, OFF by default
**The default pipeline returns RRF fusion order.** `RERANK_ENABLED` (§12,
default `false`) and the `rerank=` argument to `hybrid_search` both control it;
the explicit argument wins.

When enabled: candidates = fusion top-40 ∪ symbol-injected, deduped. Score each
(query, header + code truncated to `RERANK_PASSAGE_TOKENS`) pair with the
CrossEncoder (`RERANKER_MODEL`); return top-`SEARCH_K` ordered hits.

**Measured rationale** (DECISIONS 2026-07-26; full tables in `docs/EVAL.md`).
The cross-encoder was worse-or-equal to plain fusion at *every* k and at MRR, in
*both* corpus conditions — never better on any measured cell:

| Condition | Mode | hit@3 | hit@5 | hit@10 | MRR |
|---|---|---|---|---|---|
| implementation-only | hybrid | 0.80 | **0.90** | **0.95** | **0.755** |
| implementation-only | hybrid+rerank | 0.80 | 0.80 | 0.85 | 0.722 |
| shadowed | hybrid | 0.70 | 0.80 | **0.80** | **0.617** |
| shadowed | hybrid+rerank | 0.70 | 0.75 | 0.75 | 0.604 |

The failure is not marginal: on q09 the reranker moved a truth chunk from fusion
rank 4 to rank 38, and on q14 from rank 5 to rank 20, preferring chunks whose
identifiers echo the question's surface vocabulary (`Response.iter_text`,
`aiter_raw`) over the terse implementation that does the work. Cost of keeping
it on: ~2.4 GB resident for ≤0 measured value.

The model stays **wired and lazily loaded**, and `scripts/eval.py` keeps its
`hybrid+rerank` mode, so the ablation is permanently measurable rather than
deleted — the same philosophy as `include_tests` (§5.4).

**Consequence — §5.2 injection rides the rerank path.** Injected chunks carry no
RRF score, so fusion-only mode has nothing to order them by. Disabling rerank
therefore also disables injection. This is the exact configuration measured at
`hybrid` hit@10 0.95; re-attaching injection to the default path would be a new,
unmeasured pipeline and needs its own eval.

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

### 6.3 Test symbols — flag-and-filter
Symbols and edges are extracted from **all** files, tests included;
`symbols.is_test` carries the file's §2.6 classification. Filtering happens
at the tool layer, not at extraction — same philosophy as §2.6/§5.4:
classify at ingest, decide at query time, keep the counterfactual measurable.

Tool defaults exclude the test side:

| Tool | Default behaviour | Override |
|---|---|---|
| `get_definition` | skips definitions in test files | `include_tests=True` |
| `find_references` | excludes edges whose **from**-side symbol is a test | `include_tests=True` |
| `expand_context(direction="in")` | same from-side exclusion | — |
| `expand_context(direction="out")` | unaffected in practice — implementation rarely calls into tests | — |

The asymmetry is deliberate: an incoming edge from a test tells you a test
exercises the symbol, which is noise when the question is "who uses this?".
Outgoing edges from implementation almost never land in tests, so filtering
there would cost complexity for no measured benefit.

## §7 Agent

### 7.1 Tools — exact signatures
All tools return JSON-serializable dicts. Failures return
`{"error": "<message>"}` instead of raising — the model should read the
error and adapt.

```python
search_code(query: str, k: int = 10) -> {"hits": [SearchHit]}
    # Calls retrieval.hybrid_search() with its DEFAULT configuration: RRF
    # fusion over implementation chunks, rerank OFF (§5.3), tests excluded
    # (§5.4). The agent must not pass rerank=True — the reranker measured
    # worse-or-equal to plain fusion at every k and MRR (DECISIONS 2026-07-26).

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

**The chat model is provider-configurable via `AGENT_MODEL`** (Gemini /
Claude / Vertex), constructed only by `app/agent/model.py` — prefix-dispatched,
with retry/backoff configured on the client. Tool binding stays
provider-agnostic (`.bind_tools` on whatever the factory returns). See
DECISIONS 2026-07-26 for the cost rationale and the measurement rules
(model id recorded in every results block; stuffed-vs-agent comparisons are
within-model only).

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

**Implementation-side only, capped at 8.** The block draws from incoming
edges whose from-side symbol is *not* a test (§6.3) — on a well-tested repo
the callers of any given function are mostly its tests, which would crowd
out the real call sites this block exists to surface. Cap at 8 callers,
then `… +N more`; an unbounded list is a context-budget leak on hot symbols.
Emit nothing when there are no implementation-side callers.

### 7.5 Citations
The server regex-parses `[<path>:<start>-<end>]` markers from the final
answer, validates paths against `files`, and emits a `citations` event
(§9). Invalid paths are dropped from the event but left in the text.

## §8 HTTP API

FastAPI, JSON, Pydantic v2 models in `app/api/schemas.py`.

```
POST /repos            {"url": "https://github.com/owner/repo"}   url ≤ 500 chars
  201 RepoOut (created, job enqueued) | 200 RepoOut (URL already known)
  422 invalid/non-GitHub URL
  429 too many ingests queued/running, or per-IP rate limit
GET  /repos            {"repos": [RepoOut]}
GET  /repos/{id}       RepoOut | 404
GET  /repos/{id}/files?path=src/auth.py[&start_line=&end_line=]
  {"path": str, "content": str, "n_lines": int,
   "start_line": int, "end_line": int} | 404
  304 when If-None-Match matches the ETag
  422 end_line < start_line
  (powers the frontend code viewer and citation clicks)
POST /repos/{id}/chat  {"question": str}          question 1..4000 chars
  → text/event-stream (§9)
  409 {"detail": "repo not ready", "status": "<current>"} if not ready
  404 unknown repo
  429 every answer slot busy, or per-IP rate limit
GET  /health           {"ok": true}                      liveness only
GET  /ready            {"ok": bool, "checks": {name: {"ok": bool,
                                                      "detail": str | None}}}
                       200 when servable, 503 otherwise
GET  /metrics          Prometheus text exposition (optional bearer token)
```

Every error body is `{"detail": str, "request_id": str, ...}` and every
response carries an `X-Request-ID` header. 5xx bodies say only
`"internal server error"`: the real message is in the server log under that
same id (`app/redact.py` explains why the ones that *are* shown are redacted).
Request bodies are capped at 64 KB (413).

`GET /repos/{id}/files` is cacheable — a strong `ETag` over
`head_sha + path + range` and `Cache-Control: immutable`, because the content
of a pinned commit cannot change. `n_lines` is always the whole file; a range
narrows `content`, `start_line`, and `end_line` only. Repos with no `head_sha`
yet get `no-store` and no ETag: there is nothing stable to name.

`/health` deliberately touches nothing. A liveness probe that fails when
Postgres is down gets the process restarted for someone else's outage;
`/ready` is the one that answers "can this process serve a request", and it is
what a load balancer should route on.

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

Submitted URLs are normalized to `https://github.com/owner/repo` before the
uniqueness check, so `github.com/Owner/repo`, a `.git` suffix, and a pasted
`/blob/main/...` deep link all land on one row rather than three. Re-submitting
a known URL re-queues an ingest (200) unless a job is already in flight, in
which case the row is returned untouched.

CORS allows one origin, from `FRONTEND_ORIGIN` (default
`http://localhost:3000`), and exposes `X-Request-ID`. `POST /repos` returns
**503** when Redis is unreachable: ingestion never runs in the handler (hard
rule 1), so there is no inline fallback.

**Limits** (DECISIONS 2026-07-28; values in `app/config.py`, all env-tunable).
Per-IP fixed-window rate limits counted in Redis — tight on `POST /repos` and
`POST /chat`, generous elsewhere, and `/health`, `/ready`, `/metrics` exempt.
A 429 always carries `Retry-After`. Concurrent agent runs are capped
(`CHAT_MAX_CONCURRENCY`) and refused immediately rather than queued: a caller
parked behind eight agent runs holds a socket open for minutes to be served
badly. Concurrent ingests are capped by `MAX_ACTIVE_INGESTS`. The limiter
**fails open** if Redis is unreachable — that outage already costs the ingest
queue, and refusing reads too would make it total.

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
event: error        data: {"message": "...", "request_id": "..."}
```

`tool_result` payloads carry locations and summaries only — never full
code bodies over the wire. The frontend renders steps from these and
fetches code via `/files` on demand.

A run ends in `done` or in exactly one `error`. `error.message` is redacted
(`app/redact.py`) and paired with the `request_id` that finds the unredacted
server-side log line. Three things end a run early: the §7.2 tool cap (which
produces a normal answer), `CHAT_TIMEOUT_S` (an `error` event), and a client
disconnect (the run is cancelled, and nothing further is sent or billed).

## §10 Ingestion job lifecycle

```
queued → cloning → parsing → linking → embedding → ready
   └────────┴─────────┴──────────┴─────────┴────→ failed (error recorded)
```

Pipeline order: **clone → filter → parse → symbols → embed → backfill**. One
function, `app/ingest/pipeline.py::run_ingest(repo_id)`, is called by both the
ARQ task and the ingest CLI (Phase 4).

- `linking` is the §6.1 symbol pass, which runs while the clone is still on
  disk. It gets its own state because it is 30–40 % of wall time on a
  mid-size repo and was otherwise invisible inside `parsing`.
- Everything through the symbol pass runs **inside the clone context** (Jedi
  resolves against real files); the `symbol_id` backfill runs **after** the
  chunk insert, since it joins chunks to symbols.
- The task starts by deleting existing `files/chunks/symbols/edges` rows for
  the repo (delete-and-replace idempotency), then walks the states, updating
  counters and `updated_at` as it goes.
- `job_timeout = 900s`, `max_tries = 2`. A retry re-enters cleanly because of
  the delete-and-replace start. An ARQ cancellation (timeout/abort) stays
  retryable and is *not* recorded as `failed`.
- Zombie sweep: on worker startup, any repo in an in-flight state
  (`cloning|parsing|linking|embedding`) with `updated_at` older than
  `ZOMBIE_AFTER_S` → `failed("worker died")`. `queued` is excluded: that job
  lives in Redis, which redelivers it when a worker returns.
- Progress writes are batched (every `PROGRESS_EVERY_N` files/chunks),
  not per-row.
- Worker `poll_delay` is 2s, not ARQ's 0.5s default — a managed-Redis command
  budget, not a latency choice (DECISIONS 2026-07-27).

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
| `TEST_DIR_SEGMENTS` | `{"tests", "test", "testing"}` | §2.6 |
| `TEST_FILE_NAMES` | `{"conftest.py"}` | §2.6 |
| `CALLED_BY_MAX` | 8 | §7.4 |
| `MAX_FILE_BYTES` | 500_000 | §2.2 |
| `MAX_FILES` | 10_000 | §2.2 |
| `CHUNK_TOKEN_MAX` | 480 | §2.5 |
| `NAIVE_CHUNK_CHARS` | 1_000 | §2.7 |
| `NAIVE_CHUNK_OVERLAP_CHARS` | 100 | §2.7 |
| `VEC_K` / `FTS_K` | 40 / 40 | §5.1 |
| `RRF_K` | 60 | §5.1 |
| `SEARCH_K` | 10 | §5.3 |
| `RERANK_PASSAGE_TOKENS` | 512 | §5.3 |
| `RERANK_ENABLED` | `false` (env-overridable) | §5.3 |
| `READ_MAX_LINES` | 400 | §7.1 |
| `EXPAND_MAX_DEPTH` | 2 | §7.1 |
| `EXPAND_TOKEN_BUDGET` | 6_000 | §7.1 |
| `AGENT_TOOL_CAP` | 8 | §7.2 |
| `JEDI_FILE_TIMEOUT_S` | 10 | §6.1 |
| `ZOMBIE_AFTER_S` | 1_200 | §10 |
| `PROGRESS_EVERY_N` | 25 | §10 |

Constants live in `app/config.py` under these exact names so SPEC and
code stay greppable against each other.
