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

> **The ~20% budget was calibrated on one repo and does not hold (2026-08-01).**
> httpx measures **4%**. `pallets/flask`, ingested as the second benchmark,
> measures **52%** (imports 45%, calls 54%, extends 45%) — 2.6× the budget and
> 13× httpx. Edge density across every indexed repo puts httpx alone at 1.92
> edges/symbol and every other at ≤1.07, so httpx is the outlier the budget was
> written from, not the norm.
>
> This is the graph the product claims as its differentiator over plain
> retrieval, so a repo where it is half as dense is a real limitation rather
> than a tuning note. The cause is **not diagnosed**: `src/`-layout packaging
> breaking Jedi's project root is the obvious suspect, but two flat small repos
> also sit low, so layout is not established. Numbers and the open question:
> `docs/EVAL-FLASK.md`.

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
GET  /repos/{id}/overview[?retry=true]              OverviewOut  (§19.4)
  200 ready | 202 generating | 404 unknown/unowned
GET  /repos/{id}/architecture[?include_tests=true]   ArchitectureOut | 404  (§18.2)
GET  /repos/{id}/coverage?path=src/auth.py          CoverageOut | 404      (§18.3)
  422 missing `path`
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
| `ARCH_MAX_NODES` | 200 | §18.1 |
| `ARCH_MAX_EDGES` | 1_000 | §18.1 |
| `COVERAGE_MAX_LINKS` | 500 | §18.2 |
| `OVERVIEW_MAX_MODULES` | 15 | §19.2 |
| `OVERVIEW_MAX_ENTRY_POINTS` | 8 | §19.2 |
| `OVERVIEW_MAX_API_SYMBOLS` | 25 | §19.2 |
| `OVERVIEW_MAX_KEY_SYMBOLS` | 15 | §19.2 |
| `ENTRY_POINT_FILENAMES` | `{__main__.py, cli.py, main.py, app.py, server.py, manage.py}` | §19.2 |
| `ZOMBIE_AFTER_S` | 1_200 | §10 |
| `PROGRESS_EVERY_N` | 25 | §10 |
| `USER_DAILY_TOKEN_BUDGET` | 1_000_000 (env-overridable) | §17.6 |
| `USER_CHAT_CONCURRENCY` | 2 | §17.7 |
| `MODEL_TOKEN_COST` | per-model USD/1K in+out rate table | §17.2 |

Constants live in `app/config.py` under these exact names so SPEC and
code stay greppable against each other.

---

## §13 Identity & tenancy (v2 phase V1)

v1 had one user and no auth. This section is the contract for making the
system multi-user. It is v2 work: nothing here applies while a v1 phase is
open. Rationale for every choice: DECISIONS 2026-07-29.

### 13.1 Provider — GitHub OAuth only

One provider, no signup form, **no password ever stored**. The `users` table
holds no credential material. Two consequences that are the point rather than
side effects: there is nothing to leak, and the OAuth token is exactly the
credential a private-repo clone will need (deferred, but this is the door).

Implemented by hand in `app/auth/`, no new dependency, following the 2026-07-28
precedent that set `slowapi` and `prometheus-client` aside for the same reason.
`httpx` moves from the dev group to the main dependencies — it is the client
for the two GitHub calls, and it was already present.

### 13.2 Schema

```sql
-- 006
CREATE TABLE users (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  github_id   BIGINT NOT NULL UNIQUE,   -- immutable; the join key, not `login`
  login       TEXT NOT NULL,            -- mutable: GitHub lets you rename
  name        TEXT,
  avatar_url  TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE user_repos (
  user_id   UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  repo_id   UUID NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
  added_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, repo_id)
);
CREATE INDEX user_repos_repo ON user_repos (repo_id);
```

`github_id` is the identity, never `login` — GitHub accounts can be renamed,
and a renamed account that re-registers as a new row silently orphans a
library. `login` is refreshed on every sign-in.

Purely additive: no existing table is altered. Existing repo rows are backfilled
to a bootstrap user so nothing orphans (§13.7).

### 13.3 Flow

```
GET  /auth/github/login     302 → github.com/login/oauth/authorize
                            sets `oauth_state` cookie (HttpOnly, SameSite=Lax)
GET  /auth/github/callback?code=&state=
                            302 → FRONTEND_ORIGIN, sets `session` cookie
                            400 on state mismatch or a GitHub error
POST /auth/logout           204, clears the session cookie
GET  /auth/me               UserOut | 401
```

`state` is a random 32-byte URL-safe token, stored in a short-lived cookie and
compared on return. A callback whose `state` does not match the cookie is
refused — without it, an attacker can complete a login in a victim's browser
against their own GitHub account (login CSRF).

The backend owns the whole dance because it owns the client secret. The
frontend never sees it.

### 13.4 Session token

An opaque signed string, not a JWT — there is no third party to verify it and
no claim to carry beyond a user id:

```
v1.<base64url(user_id)>.<base64url(expiry_unix)>.<base64url(hmac_sha256)>
```

signed with `SESSION_SECRET` over the first three fields, compared with
`hmac.compare_digest` (constant time). Expiry is `SESSION_TTL_S`, checked
before the signature is trusted for anything.

Delivered **both** ways, deliberately:

* **`session` cookie** — HttpOnly, SameSite=Lax, `Secure` when the frontend
  origin is https. This is what the browser uses; HttpOnly means XSS cannot
  read it.
* **`Authorization: Bearer <token>`** — accepted on every route, and what the
  CLIs and tests use. The cookie is checked first, the header second.

No refresh tokens, no rotation, no server-side session table in V1. Signing out
clears the cookie; a stolen bearer token is valid until it expires. Both are
`user_sessions`-table work and are deferred with that noted here rather than
discovered later.

**Deployment constraint, and the one thing that will break first.** `SameSite`
is evaluated against the registrable domain, not the origin — ports are
ignored. So `localhost:3000` → `localhost:8000` is *same-site*, and `Lax`
cookies flow in local development. A deployment that puts the frontend and API
on different domains (`app.vercel.app` → `api.fly.dev`) is **cross-site**, and
`Lax` means the browser sends no cookie at all: every request arrives
anonymous and 401s, with nothing in either log to say why.

Three ways out, in preference order:

1. **Serve both from one site** — API under `/api` on the frontend's domain, or
   the frontend behind the API. Keeps `Lax`, which is the setting that actually
   defends against CSRF.
2. `SameSite=None; Secure`, which permits the cross-site cookie and gives up
   that defence — acceptable only because every state-changing route is a POST
   the API's CORS allowlist already gates.
3. Use the `Authorization` header instead of the cookie, storing the token in
   JS. Portable and CORS-friendly; it also puts the token where XSS can read
   it, which is what HttpOnly was chosen to prevent.

The frontend sends `credentials: "include"` on every call including the SSE
stream, which is necessary for all three and sufficient for the first two.

### 13.5 The ownership rule

**Authorization is enforced at the route boundary and nowhere else.**

`_require_repo` becomes `_require_owned_repo(conn, user_id, repo_id)`, which
joins `user_repos`. Every `/repos/*` route calls it. The six agent tools (§7.1)
already scope every query by `repo_id`, so a route that resolved an *owned*
repo makes everything downstream safe by construction.

Tools MUST NOT take a `user_id` or repeat the check. It sounds strictly safer
and is not: six more places to get wrong, six more tests, and a user identity
pushed into a layer that has no other reason to know users exist.

**An unowned repo returns 404, never 403.** A 403 confirms the UUID names a
real repo, which is the fact being protected. `RepoNotFoundError` already maps
to 404, so this costs nothing.

### 13.6 §8 amendments

Every `/repos/*` route requires authentication and gains `401` to its status
list. `GET /repos` returns only the caller's repos. `POST /repos` adds a
`user_repos` row on both the 201 and 200 paths — a second user submitting a
known URL *joins* it, and gets 200.

Rate limits (2026-07-28) re-key from IP to `user_id` on authenticated routes;
`/auth/*` stays per-IP, since there is no user yet. `/health`, `/ready`,
`/metrics` stay exempt and unauthenticated.

The §9 SSE event schema does not change.

### 13.7 Migration of existing data

`006` backfills every existing `repos` row to a single bootstrap user, resolved
from `BOOTSTRAP_GITHUB_ID` if set and otherwise created as a placeholder that
the first real sign-in with that `github_id` adopts. No repo is left without an
owner, because an unowned repo is unreachable through §13.5 and would look like
data loss.

### 13.8 Constants

| Name | Value | Used in |
|---|---|---|
| `SESSION_TTL_S` | 1_209_600 (14 days) | §13.4 |
| `OAUTH_STATE_TTL_S` | 600 | §13.3 |
| `SESSION_COOKIE` | `"session"` | §13.4 |
| `OAUTH_STATE_COOKIE` | `"oauth_state"` | §13.3 |
| `GITHUB_AUTHORIZE_URL` | `https://github.com/login/oauth/authorize` | §13.3 |
| `GITHUB_TOKEN_URL` | `https://github.com/login/oauth/access_token` | §13.3 |
| `GITHUB_API_USER_URL` | `https://api.github.com/user` | §13.3 |
| `GITHUB_SCOPES` | `"read:user"` | §13.3 |

`GITHUB_SCOPES` is deliberately minimal: V1 reads an identity and nothing else.
Private-repo cloning would need `repo`, which is a scope escalation every user
sees on the consent screen — a v3 decision, not a quiet default.

New env (`app/config.py`, §Environment): `GITHUB_CLIENT_ID`,
`GITHUB_CLIENT_SECRET`, `SESSION_SECRET`, optional `BOOTSTRAP_GITHUB_ID`.
Callback URL registered with GitHub is `<API origin>/auth/github/callback`.

---

## §14 Corpus snapshots (v2 phase V2)

Splits *what was ingested* from *who can see it*. A repo at a commit becomes a
shared, immutable artifact; users hold references to it rather than copies.

### 14.1 The bug this closes

`pipeline.py` calls `clear_repo_graph()` and `clear_repo_content()` at the
**start** of every ingest, and `repos.url` is globally UNIQUE. So one user
re-ingesting `encode/httpx` deletes the corpus another user is mid-chat on —
their `search_code` returns rows from a half-deleted table, silently. V1 made
this reachable by any signed-in user rather than only the operator.

The fix is not a lock. It is to stop mutating a corpus anyone might be reading:
**a ready snapshot is never written to again.** A new commit is a new snapshot.

### 14.2 Schema

```sql
-- 007
CREATE TABLE repo_sources (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  url        TEXT NOT NULL UNIQUE,   -- canonical; no `#naive` fragment
  name       TEXT NOT NULL,          -- "owner/repo"
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE repo_snapshots (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id       UUID NOT NULL REFERENCES repo_sources(id) ON DELETE CASCADE,
  commit_sha      TEXT,               -- NULL until the clone reports it
  strategy        TEXT NOT NULL DEFAULT 'ast',   -- ast | naive (§2.7)
  default_branch  TEXT,
  status          TEXT NOT NULL DEFAULT 'queued',
  error           TEXT,
  files_total     INT NOT NULL DEFAULT 0,
  files_parsed    INT NOT NULL DEFAULT 0,
  chunks_total    INT NOT NULL DEFAULT 0,
  chunks_embedded INT NOT NULL DEFAULT 0,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (source_id, commit_sha, strategy)
);
```

**`strategy` is in the unique key, and this is not optional.** The plan in V2.md
said `UNIQUE (source_id, commit_sha)`; the live data disproves it. The AST and
naive corpora of httpx sit at the *same* commit `b5addb64` and are currently
kept apart only by the `#naive` URL fragment. Once that hack retires (§14.6)
they are one source at one commit with two corpora, and a two-column key would
reject the second.

`commit_sha` is NULL until the clone reports it. Postgres treats NULLs as
distinct in a unique index, so several queued snapshots for one source can
coexist — which is correct: they are separate attempts, and the real in-flight
dedup constraint is V3's job (§15).

`files`, `chunks`, `symbols`, `edges` gain `snapshot_id`; `user_repos.repo_id`
becomes `user_repos.snapshot_id`.

### 14.3 Immutability

| Status | May be written |
|---|---|
| `queued` → `embedding` | yes, by the one worker that owns it |
| `ready` | **never** |
| `failed` | never; a retry creates a new snapshot |

`clear_repo_graph` / `clear_repo_content` leave the normal path entirely. They
survive only to clean up a *failed* attempt's partial rows, which no reader can
reach because a non-`ready` snapshot is not servable.

### 14.4 Deduplication

The commit SHA is not known until after the clone, so dedup happens in two
places:

1. **At submit (`POST /repos`).** Get-or-create the source; link the caller. If
   a `ready` snapshot already exists for `(source, strategy='ast')`, return it —
   200, no ingest, no clone. This is the common case for a popular repo.
2. **In the worker, after the clone.** With the SHA in hand, look for another
   `ready` snapshot at `(source_id, commit_sha, strategy)`. If one exists, this
   attempt is redundant: re-point its `user_repos` rows at the existing
   snapshot, delete the redundant row, and stop. Otherwise ingest.

Checking before the clone would dedup on URL, which is wrong — two users can
submit the same URL at different commits. Checking after ingesting would dedup
nothing.

### 14.5 `POST /repos` semantics change

| Newest snapshot for the source | v1 behaviour | §14 behaviour |
|---|---|---|
| none | create + enqueue, 201 | same |
| `failed` | re-enqueue **in place** | **new snapshot** + enqueue, 201 |
| `ready` | re-enqueue in place (destructive) | **return it**, 200, no work |
| in flight | return it, 200 | same |

The `ready` row is the important change: re-submitting a URL is no longer a
destructive re-ingest. The frontend's Retry button still works, because Retry
acts on a `failed` snapshot. An explicit "re-index at the latest commit" is a
new snapshot and deferred until something asks for it.

### 14.6 The `#naive` hack retires

`NAIVE_URL_FRAGMENT` mangles a URL to `<url>#naive` purely to dodge
`repos.url`'s UNIQUE constraint, then strips it again before cloning. Under
§14.2 a baseline corpus is a snapshot with `strategy='naive'`, and both the
fragment and the `@naive` name suffix are deleted rather than relocated.

### 14.7 §8 stays the same shape

A "repo" in the HTTP API is a **snapshot**. `RepoOut.id` is a `snapshot_id`,
`url` and `name` come from the joined source. No field is added, removed, or
renamed, so the frontend needs no change — the point of the phase is a schema
that can support many users, not a new API to learn.

### 14.8 Migration is data-preserving

`007` rewrites rows; it **re-embeds nothing**. Every vector keeps its value,
which is what makes §14.9's check meaningful.

- One `repo_sources` row per distinct URL, with `#naive` stripped, so the two
  httpx rows collapse onto one source.
- One `repo_snapshots` row per existing `repos` row, **keeping the same UUID**.
  The `user_repos` FK rewrite is then a rename, not a remap, and every repo id
  already handed to a browser still resolves.
- `strategy` is derived from the `#naive` fragment; `commit_sha` from
  `repos.head_sha`.
- `repo_id` columns are **kept for one release** on every table. Dropping them
  in the same migration that adds `snapshot_id` would make the rollback a
  restore-from-backup rather than a revert.

### 14.9 Verification

**The phase lives or dies on one check: `scripts/eval.py` must reproduce the
recorded baseline question for question.** Retrieval is a pure function of the
corpus, so a moved number means data was corrupted — there is no benign
explanation. The current baseline (2026-07-29, post-tiebreaker, three identical
runs) is vector `0.75 / 0.85 / 0.90` MRR `0.722`, fts `0.55 / 0.70 / 0.80` MRR
`0.463`, hybrid `0.80 / 0.90 / 0.95` MRR `0.753`.

Supporting checks: httpx still reports `825 | 697`; a spot-checked embedding is
byte-identical to its pre-migration value; and a chat streaming against one
snapshot is provably unaffected by another ingest of the same source.

---

## §15 Job leases (v2 phase V3)

### 15.1 Correcting the premise

V2.md justified this work by claiming the startup sweep would destroy a second
worker's live job: *"Worker 2 starting up will sweep worker 1's live job."*
**That is not true of the code as written**, and the plan should not have said
it.

`sweep_zombie_repos` is already **time-based**, not startup-scoped:

```sql
WHERE status = ANY(in_flight)
  AND updated_at < now() - ZOMBIE_AFTER_S     -- 1200s
```

A live job writes progress as it goes, so its `updated_at` stays fresh and the
predicate does not match it. And `job_timeout` (900s) is deliberately below
`ZOMBIE_AFTER_S` (1200s), so ARQ cancels a wedged job *before* the sweep could
reach it. Adding a second worker is therefore **not** an emergency.

There is one real hole, and it is narrower than the plan implied: **progress
writes are incidental, not a heartbeat.** The `linking` phase writes its status
once and then runs Jedi resolution silently to completion (`pipeline.py`), so a
long-enough repo could go quiet for minutes. Today the 900s job timeout caps
that exposure. Raise `JOB_TIMEOUT_S` past 1200 without adding a heartbeat and
the sweep starts killing healthy jobs.

So leases are worth building — for worker identity, precise reaping, and the
dedup constraint in §15.3 — but they are an improvement, not a precondition.
Recorded this way because a plan that overstates a risk earns the same distrust
as one that misses it.

### 15.2 Schema

```sql
-- 009
ALTER TABLE repo_snapshots
  ADD COLUMN claimed_by   TEXT,          -- worker identity, for humans and reaping
  ADD COLUMN claimed_at   TIMESTAMPTZ,
  ADD COLUMN heartbeat_at TIMESTAMPTZ;
CREATE INDEX repo_snapshots_lease ON repo_snapshots (status, heartbeat_at);
```

`claimed_by` is `<hostname>:<pid>` — enough to find the process, and never used
as a lock (a hostname can repeat; the lease is the timestamp).

### 15.3 In-flight dedup

```sql
CREATE UNIQUE INDEX repo_snapshots_one_in_flight
  ON repo_snapshots (source_id, strategy)
  WHERE status IN ('queued','cloning','parsing','linking','embedding');
```

A partial unique index, in **Postgres** rather than a Redis lock: job state
already lives here, and a lock in the other datastore can drift from the truth
without anything noticing. Two simultaneous submissions of one repo now collide
at insert time, and the loser joins the winner's snapshot.

Note this is `(source_id, strategy)` and *not* `(source_id, commit_sha)` as
V2.md said — `commit_sha` is NULL until the clone reports it, and NULLs are
distinct in a unique index, so a commit-based constraint would permit exactly
the duplicate work it is meant to prevent.

### 15.4 The heartbeat and the sweep

* A worker claims a snapshot by setting `claimed_by`/`claimed_at`/`heartbeat_at`
  in the same statement that moves it out of `queued`.
* It refreshes `heartbeat_at` **on a timer**, independent of progress — that is
  the fix for §15.1's hole.
* The sweep reaps rows whose `heartbeat_at` is older than `LEASE_EXPIRY_S`,
  regardless of status or of which worker is starting.

`LEASE_EXPIRY_S` (120s) can be far tighter than `ZOMBIE_AFTER_S` (1200s) because
a heartbeat is unconditional: a dead worker's job is reclaimed in two minutes
instead of twenty.

### 15.5 Per-user quota

`count_active_ingests` is global today, so one user's three queued repos refuse
everybody else's first submission. It becomes a count scoped through
`user_repos`, with `MAX_ACTIVE_INGESTS` applying **per user**.

### 15.6 `max_jobs` stays 1

Ingest is CPU-bound (tree-sitter, Jedi, embedding). Throughput comes from more
worker *processes*, not more concurrent jobs inside one — raising `max_jobs`
just makes every job on that box slower.

---

## §16 Inference service (v2 phase V3)

### 16.1 Why

`embedder.py` loads sentence-transformers into whatever process imports it, so
every API replica carries a 130 MB model (2.4 GB with the reranker) and HTTP
capacity cannot be scaled independently of embedding capacity.

CLAUDE.md rule 3 — *only* `embedder.py` imports sentence-transformers — is what
makes this a small change rather than a refactor: the seam already exists.

### 16.2 Contract

```
POST /embed    {"texts": [str], "batch_size": int?}  -> {"vectors": [[float]], "dim": int}
POST /rerank   {"query": str, "passages": [str]}     -> {"scores": [float]}
GET  /health                                          -> {"ok": true, "model": str}
```

Order is significant in both directions: `vectors[i]` corresponds to `texts[i]`.
The service is stateless and holds no repo or user concept — it sees text.

### 16.3 Client

`embedder.py` gains `HttpEmbedder`, satisfying the existing `Embedder` Protocol
(including `token_len`, which the §2.5 oversize split needs — so the service
must expose tokenisation or the client must carry the same tokeniser).

`INFERENCE_URL` selects the implementation: unset keeps the in-process model, so
local development and the CLIs work unchanged with nothing new to run.

### 16.4 Two deployments, one image

Query embedding is one short string on a user's critical path; ingest embedding
is thousands of chunks, throughput-bound. Same service, different sizing —
latency-tuned for the API, batch-tuned for the workers.

### 16.5 Verification

`scripts/eval.py` must be **unchanged** with the HTTP embedder in place: same
corpus, same vectors, different transport. Any movement means the service is not
producing the same numbers the in-process model does, which is a correctness
failure and not a deployment detail.

---

## §17 Quotas & accounting (v2 phase V5)

This is the last multi-tenant phase. §13 made requests attributable to a user
and §14 made corpora immutable; §17 spends both. Every answer gets a known,
recorded cost attributed to a user, a per-user budget can refuse work before it
runs, an immutable snapshot lets a popular question be answered from cache, and
one user cannot starve another. It is the phase that turns "multi-user" into
"multi-user without one account being able to run up the bill or hog the box".

Nothing here begins while a v1 phase or an earlier v2 phase is open. The design
rationale, when V5 opens, is owed a `DECISIONS.md` entry per the working
agreement — this section is the contract, not the argument.

### 17.1 What this closes

Three gaps, each independently exploitable once there is more than one user:

* **Cost is invisible.** An agent run makes up to `AGENT_TOOL_CAP` (8) model
  calls (§7.2); nothing records how many tokens they burned or against whom. On
  a metered provider (the default is Mistral, §7.2) that is an unbounded,
  unattributable bill.
* **There is no budget.** Rate limits (2026-07-28) bound *requests per window*,
  never *work per user*. A caller inside the rate limit can still run the
  expensive path indefinitely.
* **The concurrency gate is global, not fair.** `chat_slots`
  (`routes.py:56`, `Slots(CHAT_MAX_CONCURRENCY)`) caps total concurrent answer
  streams at 8 and is first-come-first-served: one user can hold all 8 while
  everyone else gets `ServiceBusyError`. The gate protects the *box*; it does
  nothing for *fairness between users*.

### 17.2 The budget unit — tokens, not dollars

Budgets are enforced in **tokens**, and dollar cost is a derived, advisory
figure recorded alongside. This is deliberate and is the one real decision in
this section:

* Token counts are **exact and provider-independent** — the model returns
  `usage_metadata` on every response, so the enforced quantity is ground truth,
  not an estimate.
* Dollar cost is an **estimate** from a static per-model rate table
  (`MODEL_TOKEN_COST` in `app/config.py`, USD per 1K input/output tokens keyed
  by model id). Because `AGENT_MODEL` is provider-configurable (§7.2), the rate
  is whatever that model's row says, and an unknown model records `NULL` cost
  rather than a wrong one — the tokens are still recorded.

Enforcing on the exact quantity and reporting the estimate keeps the refusal
deterministic (a user is never refused because a price guess drifted) while
still answering "what did this cost". Consistent with the project's
measure-don't-guess line: the number you act on is measured; the number you
show is labelled an estimate.

### 17.3 Schema

```sql
-- 011
CREATE TABLE agent_runs (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  snapshot_id   UUID NOT NULL REFERENCES repo_snapshots(id) ON DELETE CASCADE,
  question_hash BYTEA NOT NULL,            -- sha256 of the normalised question (§17.5)
  model         TEXT NOT NULL,             -- the AGENT_MODEL that answered
  input_tokens  INTEGER NOT NULL,
  output_tokens INTEGER NOT NULL,
  est_cost_usd  NUMERIC(10,6),             -- NULL when the model has no rate row
  tool_calls    SMALLINT NOT NULL,         -- ≤ AGENT_TOOL_CAP
  cache_hit     BOOLEAN NOT NULL DEFAULT false,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX agent_runs_user_day ON agent_runs (user_id, created_at);

CREATE TABLE answer_cache (
  snapshot_id   UUID NOT NULL REFERENCES repo_snapshots(id) ON DELETE CASCADE,
  question_hash BYTEA NOT NULL,
  answer        TEXT NOT NULL,
  citations     JSONB NOT NULL,            -- the exact §7.5 citation list
  tool_calls    SMALLINT NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (snapshot_id, question_hash)
);
```

`agent_runs` is the accounting ledger: one row per answered question, written
**with the run** inside the same request, so a crash after answering cannot lose
the charge. `answer_cache` is keyed on `(snapshot_id, question_hash)` and on
nothing else — see §17.5 for why that is exactly the safe key and no broader
one is.

Both tables cascade on `users`/`repo_snapshots` deletion; neither is on the
retrieval path, so neither carries an HNSW or tsvector column.

### 17.4 Token accounting

The agent does not capture usage today (`app/agent/`). V5 adds it at the model
seam: each `AIMessage` the loop receives carries `usage_metadata`
(`input_tokens` / `output_tokens`), and the graph sums them across every turn of
the run, then writes one `agent_runs` row when the loop finishes — cap-forced
answer included (§7.2). A cache hit writes a row too, with `cache_hit = true`,
`input_tokens = output_tokens = 0`: the answer is real work delivered, and the
ledger should show it was served for free.

Cost is `input_tokens/1000 * rate.in + output_tokens/1000 * rate.out` looked up
in `MODEL_TOKEN_COST[model]`, or `NULL` if the model has no row. Queryable per
user per day directly off `agent_runs (user_id, created_at)`.

### 17.5 The answer cache — why this key is safe

Cache key: `sha256(normalise(question))` under `snapshot_id`. `normalise` is
lower-casing and whitespace-collapsing only — deliberately conservative, because
two questions that normalise together must be genuinely the same question.

**This is only correct because §14 made snapshots immutable.** An answer is a
pure function of `(snapshot corpus, question)`: the retrieval query and the
symbol graph are fixed for a snapshot, so the same question against the same
snapshot yields the same citations. Cache **across** snapshots is forbidden
(V2.md "Do not") — a different snapshot is a different corpus, so a hit there
would return citations into the wrong commit's files. The key carries
`snapshot_id` for exactly this reason.

A hit MUST return **byte-identical citations** to the uncached run — that is the
verification criterion (§17.9), and it is what proves the cache is a cache and
not a subtly different code path. The cached answer is replayed as a well-formed
§9 SSE stream (text deltas, then `citations`, then `done`), so the client cannot
tell a hit from a miss and **the §9 event schema does not change** — the same
guarantee §13.6 and §14.7 make. A hit runs the agent zero times, so it also
costs zero tokens and cannot push a user over budget.

Cache entries never expire: an immutable snapshot's answer cannot go stale.
Reclaiming them is snapshot retention/GC, which is v3 backlog (V2.md) — until a
snapshot is deleted (cascading the cache with it), its answers stay.

### 17.6 Budgets and refusal

A user whose rolling-24h token total (summed from `agent_runs`) is at or over
`USER_DAILY_TOKEN_BUDGET` is refused **before** the agent runs, with the
existing typed-exception path: a `BudgetExceededError(TooManyRequestsError)`
sibling of `ServiceBusyError` (`app/exceptions.py`), carrying `retry_after`
(seconds until the oldest counted run ages out of the window) and `rule`. It
maps to **429** through `errors.py`'s existing `TooManyRequestsError` handler —
same `{detail, request_id}` envelope and `Retry-After` header as every other
limit. No new mapping, no new shape.

The check is read-then-run, not a reservation: a run already in flight is not
pre-charged, so a user at 99% of budget can start one more expensive answer.
This is the same shape as the rate limiter and is intentional — a token budget
bounds sustained spend, not a single over-shoot, and pre-charging would need a
worst-case estimate the token count exists precisely to avoid.

### 17.7 Fairness — per-user concurrency before the global gate

A per-user answer-slot check runs **ahead of** the global `chat_slots` gate in
the chat route (`routes.py:387`). A user already streaming
`USER_CHAT_CONCURRENCY` answers is refused with `ServiceBusyError` even when
global slots are free; only after passing the per-user check does the request
try the global gate.

Ordering is the whole point (V2.md): fairness is enforced *ahead of* capacity,
so one user cannot occupy more than their share while another is turned away.
Implemented as a small per-`user_id` slot map beside `chat_slots`, each entry a
`Slots` (`app/api/ratelimit.py`) — process-local, like `chat_slots`, and
released on the same `finally` that releases the global slot.

### 17.8 Observability

`user_id` joins `request_id` on every log line, using the **same seam**: a
`_user_id` `ContextVar` in `logging_setup.py` next to `_request_id`
(`logging_setup.py:38`), set in the request-context middleware once the
`CurrentUser` (§13) is resolved, and added to both `FORMAT`
(`logging_setup.py:27`) and the JSON formatter's dict. Unauthenticated routes
log the `NO_REQUEST`-style sentinel.

`user_id` is **never** a Prometheus label. It is unbounded cardinality — one
series per user — and the 2026-07-28 entry already rejected raw-path labelling
for that exact reason. Per-user numbers live in `agent_runs` and are queried
with SQL, not scraped. Metrics stay aggregate (`app/metrics.py`).

### 17.9 Verification

* Every answered question writes an `agent_runs` row with token counts and (when
  the model has a rate) an estimated cost, attributed to the caller; a
  per-user-per-day total is a single SQL query. A run that hits `AGENT_TOOL_CAP`
  and force-answers still records its tokens.
* An over-budget user is refused with **429**, a `Retry-After` header, and the
  standard `{detail, request_id}` body — asserted in a test that seeds
  `agent_runs` past `USER_DAILY_TOKEN_BUDGET`, not by waiting for real spend.
* A cache hit returns **citations byte-identical** to the uncached run for the
  same `(snapshot_id, question)` — proven by running the question twice and
  diffing the `citations` payloads, and by an `agent_runs` row with
  `cache_hit = true` and zero tokens. A different snapshot for the same question
  is a miss.
* One user holding `USER_CHAT_CONCURRENCY` streams is refused their next while a
  **different** user's stream is admitted in the same instant — verified by test
  against the per-user gate, mirroring the §15.5 per-user-quota test shape.
* `user_id` is present on every log line and on no Prometheus series.

Do not: cache across snapshots; enforce the budget in estimated dollars; label a
metric by `user_id`; pre-charge a budget reservation; add a background job to
expire cache rows (that is snapshot GC, and it is v3 backlog).

---

## §18 Graph views (read-only aggregations)

Two endpoints that answer questions from the symbol graph **without a model**.
They exist because the graph already holds the answers and nothing in the
product had ever asked it directly: every path to the `symbols`/`edges` tables
ran through an agent tool.

### 18.1 Why these are endpoints and not agent tools

The agent has a hard budget of `AGENT_TOOL_CAP` (8) tool executions per run
(§7.2), and Phase 5's live run reached it. A seventh and eighth tool do not just
need a DECISIONS entry — they change how the existing eight executions get
spent, which is a measurable risk to answer quality.

Both questions here have **exact, deterministic answers in SQL**. Routing them
through the model would spend a scarce budget to compute something a `GROUP BY`
already knows, and would make the result non-reproducible for no gain. So they
are endpoints: the frontend calls them directly, they cost no tool calls, no
tokens, and no quota (§17), and the agent loop is untouched.

The rule this sets: **if the symbol graph can answer it exactly, it is a query;
the agent is for what needs judgement.**

### 18.2 Module rollup — `GET /repos/{id}/architecture`

In Python a file *is* a module, so `symbols.file_path` is the module key
directly. Nothing is parsed out of the path, which is what guarantees the
rollup cannot disagree with the graph it summarises.

```
ArchitectureOut {
  nodes: [{path, n_symbols, fan_in, fan_out}]
  edges: [{from_path, to_path, kind, weight}]
  include_tests: bool
  truncated: bool
}
```

* **Same-file edges are excluded** from both fan counts and from `edges`. A
  module calling itself says nothing about architecture, and on a large file it
  would dominate the ranking.
* `weight` is the number of symbol-level edges of that `kind` crossing the pair,
  so a renderer can distinguish "deeply coupled" from "one import".
* Ranked by `fan_in DESC, file_path` — the tiebreaker is not optional, or the
  truncation at `ARCH_MAX_NODES` would select a different top-N per physical row
  order (DECISIONS 2026-07-29).
* `include_tests` defaults to **false**, per §6.3 flag-and-filter: extraction
  kept every symbol, the decision happens at query time, the counterfactual is
  one parameter away.
* `truncated` is set when either list hit its §12 cap. Caps are a SQL `LIMIT`,
  not a slice of an already-materialised graph — the cost being avoided is
  Postgres building the full rollup for a 10_000-file repo.

### 18.3 Test ↔ code linkage — `GET /repos/{id}/coverage?path=`

```
CoverageOut {
  path: str
  covered: [{name, qualname, kind, start_line, end_line,
             tests: [{qualname, file_path, line}]}]
  covers:  [{qualname, file_path, line}]
  truncated: bool
}
```

* `covered` is the mirror of §7.4's called-by assembly, which excludes the test
  side precisely because it is noise when the question is "who uses this?". Here
  the test side *is* the question, so the filter is **inverted, not dropped**: a
  caller from another implementation file is not coverage.
* One entry per symbol, tests grouped under it, in definition order — so a
  viewer can walk the open file top to bottom.
* `covers` is the reverse direction and is empty for a non-test file. That is
  the true answer, not a missing case: an implementation file does not cover
  anything, and reporting its outgoing call edges here would be a different
  question wearing this one's name.
* **An unknown `path` returns empty lists, not 404.** "This file is not indexed"
  and "no test reaches this file" are the same answer to the question asked, and
  separating them would make the endpoint an existence oracle for paths inside a
  repo — the §13.5 reasoning one level down.

### 18.4 Shared properties

* Ownership is enforced by `_require_owned_repo` and nowhere else (§13.5): 404
  for an unowned repo, 401 for no session.
* Neither view returns a **code body** — only pointers, exactly like a §9
  `tool_result`. `/files` remains the single endpoint that serves code, which is
  where the ETag and the line-range cap live.
* Both fall under the default per-identity rate limit; neither is expensive
  enough to name in `rules_for`.
* Deterministic over an immutable snapshot (§14.3), so a client may cache the
  response for as long as it holds the snapshot id.

### 18.5 Verification

`tests/api/test_graph_views.py` — ranking, edge weights, the `include_tests`
default, caps reaching SQL rather than being applied after the fact, grouping of
a multi-test symbol, the empty-not-404 rule, cross-tenant 404, anonymous 401,
and that neither response contains a code body.

---

## §19 Generated repo overview

The "start here" page a newcomer sees the moment a repo is indexed: what the
project does, how it is laid out, where execution starts, and what to read
first — every claim carrying a `file:line` citation. It answers the question a
blank chat box does not: **what do I even ask?**

### 19.1 Gather deterministically, synthesise once

This does **not** run the §7.2 agent loop. The loop is right for a question
nobody anticipated; an overview is the same four questions for every repo, and
all four have exact answers in the symbol graph. So the facts are assembled by
SQL and handed to a **single** model call. Three consequences, each a reason
rather than a side effect:

* **Cost.** One request per snapshot, not the eight a loop would spend. The
  tuning provider's free tier is 20 requests/day/model (`app/agent/model.py`),
  so a loop here would make a handful of repo pages a whole day's budget.
* **Reproducibility.** The input is a pure function of an immutable snapshot
  (§14.3); two runs differ only by model sampling, and the stored row means
  there is normally only ever one run.
* **Coverage.** A loop capped at eight calls sees whatever its first search
  surfaced. The rollup sees the whole graph, ranked, every time.

### 19.2 The facts (`overview.gather_facts`)

| Group | Source | Why |
|---|---|---|
| Repo identity, file count, top-level dirs | `repo_sources`, `files` | Orientation |
| Modules ranked by fan-in | §18.2 `module_nodes` | What the repo leans on |
| Entry-point candidates | `entry_point_candidates` | Where execution starts |
| Public API surface | `public_api_symbols` | What the package exports |
| Most-referenced definitions | `most_referenced_symbols` | What to read first |

**Entry points use two signals**, unioned, because neither is reliable alone: a
conventional filename (`ENTRY_POINT_FILENAMES`), and *shape* — nothing in the
repo reaches it, yet it reaches plenty, which is the signature of the top of a
call tree and catches an entry point named something the list has never heard
of. Named matches rank first.

**Public API is defined OR re-exported.** Small packages define names in
`__init__.py`; every non-trivial one re-exports them (`from ._api import get`),
which lives in the graph as an `imports` edge out of `__init__.py`. A
definitions-only query returned **zero** symbols on httpx. Measured across the
indexed corpus after unioning both: markupsafe 25, itsdangerous 17, blinker 3,
httpx 1 — httpx stays low because Jedi resolved only 2 of its re-export edges,
which is a graph limitation, not a query one.

### 19.3 The prompt (`prompts.OVERVIEW_SYSTEM`)

Four fixed `##` sections. The rules that matter, in the order breaking them
hurts:

1. **Use only the facts given.** Inference from them is wanted; new facts are
   not.
2. **No installation, dependencies, configuration, or how to run it.**
   `filters.py` keeps `*.py` only, so there is no README, manifest, or CI config
   in the corpus — anything written on those topics is recalled from other
   projects, which is precisely the failure this product exists to avoid. The
   prompt forbids it rather than leaving it to chance.
3. **Say what cannot be told.** "The graph does not show a single entry point"
   is a useful sentence; a confident guess is not.

**Every fact group ships a citable range.** Learned twice, live: entry points
had no range and the model invented `[…asgi.py:1-1]`; modules had none and it
wrote the literal `[…_models.py:1-?]`. Neither fabrication reached a reader —
both failed validation — but the claim lost its citation either way. *A fact you
want cited has to arrive with something to cite.*

**The citation contract is stated once**, by sharing §7.5's `CITATIONS` block
verbatim. An earlier version restated the rule in its own prose without the
worked CORRECT/INCORRECT contrast, and the first live run wrote
`[httpx/_models.py:382-512,515-1076,139-379]` for nearly every claim: **2 of ~15
citations survived**. After sharing the block: **21 of 25**, zero malformed. The
rule was present both times; the demonstration is what does the work.

### 19.4 Lifecycle — `GET /repos/{id}/overview`

Generated **lazily on first view**, not at the end of ingest: generation would
otherwise sit on the critical path of every ingest including the ones nobody
opens, and the lazy path gives an overview to snapshots ingested before the
feature existed.

```
200 OverviewOut {status: "ready",      body, citations, model}
202 OverviewOut {status: "generating", body: null}
200 OverviewOut {status: "failed",     error}    ?retry=true clears and re-runs
404 unknown or unowned repo (§13.5, checked before anything is claimed)
```

The model call is on the **queue**, never in the handler — a request that blocks
for tens of seconds holds a connection for all of them. The endpoint claims the
row and returns 202; `worker.generate_overview` fills it in.

**Concurrency is settled by the primary key**, not a lock. `claim_overview` is
an `INSERT … ON CONFLICT DO NOTHING`; two browsers opening the same repo both
attempt it, exactly one wins, and only that one enqueues. On a 20-per-day budget
that difference is the feature. Same reasoning as §15.3: the database is already
the source of truth and cannot drift from itself.

A `failed` row is cleared by `?retry=true` and never automatically — an
automatic retry on a model failure is a loop that drains the day's budget. It
must also be *clearable*, or one failure blocks that snapshot forever, which is
the bug `010` had to fix for snapshots.

### 19.5 Rendering

Sections are split client-side on `##` (`lib/overview.ts`) so each gets an "ask
more" link built from its own heading — which is what turns a document into a
set of doors, with no extra model output. Citations render as the same chips a
chat answer uses; the repo page has no viewer, so a chip opens the chat with the
file already in hand.

### 19.6 Verification

`tests/api/test_overview.py` — claim-once under concurrent first views, no
re-enqueue while generating or when ready, failure surfaced without auto-retry,
`?retry=true` clearing only `failed` rows, tenancy 404 *before* any claim is
spent, and the two prompt regressions above pinned as tests.
