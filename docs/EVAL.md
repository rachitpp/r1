# EVAL.md — frozen benchmark

**Benchmark repo:** [`encode/httpx`](https://github.com/encode/httpx)
**Pinned commit:** `b5addb64f0161ff6bfe94c124ef76f6a1fba5254` (branch `master`)

These 20 questions are the frozen ground truth for retrieval and answer-level
evaluation. They were written in Phase 1 by reading the repo at the pinned SHA,
**before any retrieval code existed**, so the benchmark cannot be tuned to the
implementation.

## Rules

- **Frozen once Phase 2 begins.** Do not add, remove, or reword questions or
  ground truth after the first `scripts/eval.py` run. Never hand-tune retrieval
  against individual questions (CLAUDE.md / ROADMAP). Fix retrieval, not the
  benchmark.
- **File paths** are relative to the repo root and pinned to the SHA above.
  `truth.files` is **any-of**: a question is satisfied by citing any one listed
  file. At least one file is required per question.
- **Symbols** are stored as short names. **Match rule:** a `truth.symbols`
  entry matches a chunk when the chunk's dotted `qualname` equals it **or ends
  with `"." + entry`** (e.g. `Timeout` matches `httpx._config.Timeout`;
  `build_request` matches `httpx._client.BaseClient.build_request`).
  `truth.symbols` is optional and advisory; `truth.files` is authoritative.
- **Metrics** (defined in SPEC §11.2, computed by `scripts/eval.py` in Phase 2):
  - **hit@k** (k=5, 10): 1 if any top-k `SearchHit` has `file_path ∈ truth.files`
    or `symbol` matching `truth.symbols` under the rule above.
  - **answer-hit**: the final answer contains ≥1 parsed citation whose file is
    in `truth.files`.
  - Retrieval modes: `vector` | `fts` | `hybrid` | `hybrid+rerank`.
    Answer modes: `stuffed` (top-10 chunks, no tools) vs `agent` (full loop).
- Results are appended as dated blocks by `scripts/eval.py`. **Never edit an old
  result block.**

## Question design

Three tiers: **locate** (q01–q07, intentionally easy — the answer's identifier
appears in the question), **conceptual** (q08–q15, "how does X work"), and
**flow** (q16–q20, "what happens when…"). Conceptual and flow questions are
phrased in user vocabulary so the benchmark discriminates between naive lexical
retrieval and the full graph-traversing system: **11 of 20** questions have
**zero lexical overlap** between the question text and the answer's symbol
identifiers (tokenized, snake_case/CamelCase-split, ignoring the stoplist
`{http, httpx, request, response, client, url}`). Paths below are relative to
the repo root.

## Questions

```yaml
- id: q01
  tier: locate
  question: "Where is the request Timeout configuration class defined?"
  truth:
    files: ["httpx/_config.py"]
    symbols: ["Timeout"]

- id: q02
  tier: locate
  question: "Where is the SSL context created and configured?"
  truth:
    files: ["httpx/_config.py"]
    symbols: ["create_ssl_context"]

- id: q03
  tier: locate
  question: "Where is the URL parsing function urlparse implemented?"
  truth:
    files: ["httpx/_urlparse.py"]
    symbols: ["urlparse"]

- id: q04
  tier: locate
  question: "Where is the DigestAuth authentication class defined?"
  truth:
    files: ["httpx/_auth.py"]
    symbols: ["DigestAuth"]

- id: q05
  tier: locate
  question: "Where are HTTP status codes enumerated?"
  truth:
    files: ["httpx/_status_codes.py"]
    symbols: ["codes"]

- id: q06
  tier: locate
  question: "Where is the top-level get() convenience function defined?"
  truth:
    files: ["httpx/_api.py"]
    symbols: ["get"]

- id: q07
  tier: locate
  question: "Where is the default synchronous HTTP transport implemented?"
  truth:
    files: ["httpx/_transports/default.py"]
    symbols: ["HTTPTransport"]

- id: q08
  tier: conceptual
  question: "When I pass auth=(user, pass), what does httpx actually add to the outgoing request?"
  truth:
    files: ["httpx/_auth.py"]
    symbols: ["BasicAuth"]

- id: q09
  tier: conceptual
  question: "How does httpx handle responses the server has compressed?"
  truth:
    files: ["httpx/_decoders.py"]
    symbols: ["GZipDecoder", "DeflateDecoder"]

- id: q10
  tier: conceptual
  question: "When I attach files to a request, how does httpx build the body that gets sent?"
  truth:
    files: ["httpx/_multipart.py"]
    symbols: ["MultipartStream"]

- id: q11
  tier: conceptual
  question: "How does httpx decide which proxy applies to a given URL?"
  truth:
    files: ["httpx/_client.py", "httpx/_utils.py"]
    symbols: ["_transport_for_url", "URLPattern", "get_environment_proxies"]

- id: q12
  tier: conceptual
  question: "How are URLs represented and modified once I pass one in?"
  truth:
    files: ["httpx/_urls.py"]
    symbols: ["URL"]

- id: q13
  tier: conceptual
  question: "When I pass json= or files= to a request, how does the body get produced?"
  truth:
    files: ["httpx/_content.py"]
    symbols: ["encode_request"]

- id: q14
  tier: conceptual
  question: "How does httpx turn a streamed byte body into a string as chunks arrive?"
  truth:
    files: ["httpx/_decoders.py"]
    symbols: ["TextDecoder"]

- id: q15
  tier: conceptual
  question: "Why can response.text decode with the wrong charset, and where does the charset come from?"
  truth:
    files: ["httpx/_models.py", "httpx/_decoders.py"]
    symbols: ["charset_encoding", "TextDecoder"]

- id: q16
  tier: flow
  question: "If a server replies 302, what does httpx do next and how is the follow-up built?"
  truth:
    files: ["httpx/_client.py"]
    symbols: ["_send_handling_redirects", "_build_redirect_request"]

- id: q17
  tier: flow
  question: "How does a request get retried after a 401 challenge?"
  truth:
    files: ["httpx/_client.py", "httpx/_auth.py"]
    symbols: ["_send_handling_auth", "DigestAuth"]

- id: q18
  tier: flow
  question: "What is the sequence of calls when I run httpx.get(url)?"
  truth:
    files: ["httpx/_api.py", "httpx/_client.py"]
    symbols: ["request"]

- id: q19
  tier: flow
  question: "How is a Request built from the arguments I pass before it is sent?"
  truth:
    files: ["httpx/_client.py", "httpx/_models.py"]
    symbols: ["build_request", "Request"]

- id: q20
  tier: flow
  question: "What happens end to end when a client dispatches one request?"
  truth:
    files: ["httpx/_client.py", "httpx/_transports/default.py"]
    symbols: ["_send_single_request", "HTTPTransport"]
```

## Results

_None yet. `scripts/eval.py` (Phase 2) appends dated result blocks here._

### Results — 2026-07-25

**Repo:** https://github.com/encode/httpx @ `b5addb64f016` — 1522 chunks, 20 questions. Modes: vector, fts, hybrid, hybrid+rerank.

| Mode | hit@5 | hit@10 |
|---|---|---|
| vector | 0.80 (16/20) | 0.85 (17/20) |
| fts | 0.05 (1/20) | 0.05 (1/20) |
| hybrid | 0.80 (16/20) | 0.85 (17/20) |
| hybrid+rerank | 0.75 (15/20) | 0.80 (16/20) |

Per-question hit@10:

| q | vector | fts | hybrid | hybrid+rerank |
|---|---|---|---|---|
| q01 | ✓ | · | ✓ | ✓ |
| q02 | ✓ | ✓ | ✓ | ✓ |
| q03 | ✓ | · | ✓ | ✓ |
| q04 | ✓ | · | ✓ | ✓ |
| q05 | ✓ | · | ✓ | ✓ |
| q06 | ✓ | · | ✓ | ✓ |
| q07 | ✓ | · | ✓ | ✓ |
| q08 | ✓ | · | ✓ | ✓ |
| q09 | · | · | · | · |
| q10 | · | · | · | · |
| q11 | ✓ | · | ✓ | ✓ |
| q12 | ✓ | · | ✓ | ✓ |
| q13 | ✓ | · | ✓ | ✓ |
| q14 | ✓ | · | ✓ | · |
| q15 | · | · | · | · |
| q16 | ✓ | · | ✓ | ✓ |
| q17 | ✓ | · | ✓ | ✓ |
| q18 | ✓ | · | ✓ | ✓ |
| q19 | ✓ | · | ✓ | ✓ |
| q20 | ✓ | · | ✓ | ✓ |


### Results — 2026-07-25

**Repo:** https://github.com/encode/httpx @ `b5addb64f016` — 1522 chunks, 20 questions. Modes: fts.

| Mode | hit@3 | hit@5 | hit@10 | MRR |
|---|---|---|---|---|
| fts | 0.25 (5/20) | 0.45 (9/20) | 0.65 (13/20) | 0.273 |

Per-question hit@10:

| q | fts |
|---|---|
| q01 | · |
| q02 | ✓ |
| q03 | ✓ |
| q04 | ✓ |
| q05 | ✓ |
| q06 | ✓ |
| q07 | ✓ |
| q08 | · |
| q09 | · |
| q10 | · |
| q11 | ✓ |
| q12 | ✓ |
| q13 | · |
| q14 | · |
| q15 | · |
| q16 | ✓ |
| q17 | ✓ |
| q18 | ✓ |
| q19 | ✓ |
| q20 | ✓ |


### Results — 2026-07-26

**Repo:** https://github.com/encode/httpx @ `b5addb64f016` — 1522 chunks, 20 questions. Modes: vector, fts, hybrid, hybrid+rerank.

| Mode | hit@3 | hit@5 | hit@10 | MRR |
|---|---|---|---|---|
| vector | 0.65 (13/20) | 0.80 (16/20) | 0.85 (17/20) | 0.632 |
| fts | 0.25 (5/20) | 0.45 (9/20) | 0.65 (13/20) | 0.273 |
| hybrid | 0.70 (14/20) | 0.80 (16/20) | 0.80 (16/20) | 0.617 |
| hybrid+rerank | 0.70 (14/20) | 0.75 (15/20) | 0.75 (15/20) | 0.604 |

Per-question hit@10:

| q | vector | fts | hybrid | hybrid+rerank |
|---|---|---|---|---|
| q01 | ✓ | · | ✓ | ✓ |
| q02 | ✓ | ✓ | ✓ | ✓ |
| q03 | ✓ | ✓ | ✓ | ✓ |
| q04 | ✓ | ✓ | ✓ | ✓ |
| q05 | ✓ | ✓ | ✓ | ✓ |
| q06 | ✓ | ✓ | ✓ | ✓ |
| q07 | ✓ | ✓ | ✓ | ✓ |
| q08 | ✓ | · | ✓ | · |
| q09 | · | · | · | · |
| q10 | · | · | · | · |
| q11 | ✓ | ✓ | ✓ | ✓ |
| q12 | ✓ | ✓ | ✓ | ✓ |
| q13 | ✓ | · | ✓ | ✓ |
| q14 | ✓ | · | · | · |
| q15 | · | · | · | · |
| q16 | ✓ | ✓ | ✓ | ✓ |
| q17 | ✓ | ✓ | ✓ | ✓ |
| q18 | ✓ | ✓ | ✓ | ✓ |
| q19 | ✓ | ✓ | ✓ | ✓ |
| q20 | ✓ | ✓ | ✓ | ✓ |


### Results — 2026-07-26

**Repo:** https://github.com/encode/httpx @ `b5addb64f016` — 1522 chunks (825 implementation, 697 test), 20 questions. Modes: vector, fts, hybrid, hybrid+rerank.

**Corpus condition:** implementation-only (default, is_test excluded)

| Mode | hit@3 | hit@5 | hit@10 | MRR |
|---|---|---|---|---|
| vector | 0.75 (15/20) | 0.85 (17/20) | 0.90 (18/20) | 0.722 |
| fts | 0.55 (11/20) | 0.70 (14/20) | 0.80 (16/20) | 0.465 |
| hybrid | 0.80 (16/20) | 0.90 (18/20) | 0.95 (19/20) | 0.755 |
| hybrid+rerank | 0.80 (16/20) | 0.80 (16/20) | 0.85 (17/20) | 0.722 |

Per-question hit@10:

| q | vector | fts | hybrid | hybrid+rerank |
|---|---|---|---|---|
| q01 | ✓ | · | ✓ | ✓ |
| q02 | ✓ | ✓ | ✓ | ✓ |
| q03 | ✓ | ✓ | ✓ | ✓ |
| q04 | ✓ | ✓ | ✓ | ✓ |
| q05 | ✓ | ✓ | ✓ | ✓ |
| q06 | ✓ | ✓ | ✓ | ✓ |
| q07 | ✓ | ✓ | ✓ | ✓ |
| q08 | ✓ | ✓ | ✓ | ✓ |
| q09 | · | ✓ | ✓ | · |
| q10 | · | · | · | · |
| q11 | ✓ | ✓ | ✓ | ✓ |
| q12 | ✓ | ✓ | ✓ | ✓ |
| q13 | ✓ | · | ✓ | ✓ |
| q14 | ✓ | · | ✓ | · |
| q15 | ✓ | ✓ | ✓ | ✓ |
| q16 | ✓ | ✓ | ✓ | ✓ |
| q17 | ✓ | ✓ | ✓ | ✓ |
| q18 | ✓ | ✓ | ✓ | ✓ |
| q19 | ✓ | ✓ | ✓ | ✓ |
| q20 | ✓ | ✓ | ✓ | ✓ |

**Corpus condition:** shadowed (--include-tests, is_test included)

| Mode | hit@3 | hit@5 | hit@10 | MRR |
|---|---|---|---|---|
| vector | 0.65 (13/20) | 0.80 (16/20) | 0.85 (17/20) | 0.632 |
| fts | 0.25 (5/20) | 0.45 (9/20) | 0.65 (13/20) | 0.271 |
| hybrid | 0.70 (14/20) | 0.80 (16/20) | 0.80 (16/20) | 0.617 |
| hybrid+rerank | 0.70 (14/20) | 0.75 (15/20) | 0.75 (15/20) | 0.604 |

Per-question hit@10:

| q | vector | fts | hybrid | hybrid+rerank |
|---|---|---|---|---|
| q01 | ✓ | · | ✓ | ✓ |
| q02 | ✓ | ✓ | ✓ | ✓ |
| q03 | ✓ | ✓ | ✓ | ✓ |
| q04 | ✓ | ✓ | ✓ | ✓ |
| q05 | ✓ | ✓ | ✓ | ✓ |
| q06 | ✓ | ✓ | ✓ | ✓ |
| q07 | ✓ | ✓ | ✓ | ✓ |
| q08 | ✓ | · | ✓ | · |
| q09 | · | · | · | · |
| q10 | · | · | · | · |
| q11 | ✓ | ✓ | ✓ | ✓ |
| q12 | ✓ | ✓ | ✓ | ✓ |
| q13 | ✓ | · | ✓ | ✓ |
| q14 | ✓ | · | · | · |
| q15 | · | · | · | · |
| q16 | ✓ | ✓ | ✓ | ✓ |
| q17 | ✓ | ✓ | ✓ | ✓ |
| q18 | ✓ | ✓ | ✓ | ✓ |
| q19 | ✓ | ✓ | ✓ | ✓ |
| q20 | ✓ | ✓ | ✓ | ✓ |


### Answer-level results — 2026-07-26

**Model:** `mistral-medium-latest` · **Set:** frozen 20 (measurement) · **Model calls:** 133 · Metric: answer-hit (≥1 validated citation whose file ∈ truth.files).

| Mode | answer-hit | cited | tool calls (mean/max) | errors |
|---|---|---|---|---|
| stuffed | 0.95 (19/20) | 0.95 (19/20) | 0.0 / 0 | 0 |
| agent | 0.95 (19/20) | 0.95 (19/20) | 4.7 / 8 | 0 |

Agent tool usage:

| Tool | calls | share |
|---|---|---|
| read_file | 58 | 60% |
| search_code | 27 | 28% |
| expand_context | 6 | 6% |
| get_definition | 5 | 5% |

Per-question:

| q | stuffed | agent |
|---|---|---|
| q01 | ✓ | ✓ |
| q02 | ✓ | ✓ |
| q03 | ✓ | ✓ |
| q04 | ✓ | ✓ |
| q05 | ✓ | ✓ |
| q06 | ✓ | ✓ |
| q07 | ✓ | ✓ |
| q08 | ✓ | ✓ |
| q09 | ✓ | ✓ |
| q10 | · | ✓ |
| q11 | ✓ | ✓ |
| q12 | ✓ | ✓ |
| q13 | ✓ | ✓ |
| q14 | ✓ | ✓ |
| q15 | ✓ | · |
| q16 | ✓ | ✓ |
| q17 | ✓ | ✓ |
| q18 | ✓ | ✓ |
| q19 | ✓ | ✓ |
| q20 | ✓ | ✓ |


### Answer-level results — 2026-07-26

**Model:** `vertex:gemini-2.5-flash` · **Set:** frozen 20 (measurement) · **Model calls:** 105 · Metric: answer-hit (≥1 validated citation whose file ∈ truth.files).

| Mode | answer-hit | cited | tool calls (mean/max) | errors |
|---|---|---|---|---|
| stuffed | 0.95 (19/20) | 1.00 (20/20) | 0.0 / 0 | 0 |
| agent | 1.00 (20/20) | 1.00 (20/20) | 3.2 / 8 | 0 |

Agent tool usage:

| Tool | calls | share |
|---|---|---|
| get_definition | 22 | 33% |
| search_code | 20 | 30% |
| expand_context | 16 | 24% |
| read_file | 8 | 12% |
| find_references | 1 | 1% |

Per-question:

| q | stuffed | agent |
|---|---|---|
| q01 | ✓ | ✓ |
| q02 | ✓ | ✓ |
| q03 | ✓ | ✓ |
| q04 | ✓ | ✓ |
| q05 | ✓ | ✓ |
| q06 | ✓ | ✓ |
| q07 | ✓ | ✓ |
| q08 | ✓ | ✓ |
| q09 | ✓ | ✓ |
| q10 | · | ✓ |
| q11 | ✓ | ✓ |
| q12 | ✓ | ✓ |
| q13 | ✓ | ✓ |
| q14 | ✓ | ✓ |
| q15 | ✓ | ✓ |
| q16 | ✓ | ✓ |
| q17 | ✓ | ✓ |
| q18 | ✓ | ✓ |
| q19 | ✓ | ✓ |
| q20 | ✓ | ✓ |


### Answer-level results — 2026-07-27

**Model:** `mistral-medium-latest` · **Set:** frozen 20 (measurement) · **Model calls:** 141 · Metric: answer-hit (≥1 validated citation whose file ∈ truth.files).

| Mode | answer-hit (file) | symbol-hit | cited | tool calls (mean/max) | errors |
|---|---|---|---|---|---|
| stuffed | 0.90 (18/20) | 0.75 (15/20) | 0.95 (19/20) | 0.0 / 0 | 0 |
| agent | 0.90 (18/20) | 0.85 (17/20) | 0.90 (18/20) | 5.0 / 8 | 0 |

Agent tool usage:

| Tool | calls | share |
|---|---|---|
| read_file | 58 | 55% |
| search_code | 32 | 30% |
| get_definition | 13 | 12% |
| expand_context | 2 | 2% |

Graph-tool use vs correctness (agent):

| Agent run | symbol-hit | symbol-miss |
|---|---|---|
| used a graph tool | 7 | 0 |
| no graph tool | 10 | 3 |

Per-question:

| q | stuffed | agent |
|---|---|---|
| q01 | ✓s | ✓s |
| q02 | ✓s | ✓s |
| q03 | ✓s | · |
| q04 | ✓s | ✓s |
| q05 | ✓s | ✓s |
| q06 | ✓s | ✓s |
| q07 | · | · |
| q08 | ✓s | ✓s |
| q09 | ✓ | ✓ |
| q10 | · | ✓s |
| q11 | ✓s | ✓s |
| q12 | ✓s | ✓s |
| q13 | ✓s | ✓s |
| q14 | ✓ | ✓s |
| q15 | ✓s | ✓s |
| q16 | ✓s | ✓s |
| q17 | ✓s | ✓s |
| q18 | ✓s | ✓s |
| q19 | ✓s | ✓s |
| q20 | ✓ | ✓s |

`✓s` = file + symbol · `✓` = file only · `·` = miss


### Answer-level results — 2026-07-27

**Model:** `vertex:gemini-2.5-flash` · **Set:** frozen 20 (measurement) · **Model calls:** 106 · Metric: answer-hit (≥1 validated citation whose file ∈ truth.files).

| Mode | answer-hit (file) | symbol-hit | cited | tool calls (mean/max) | errors |
|---|---|---|---|---|---|
| stuffed | 0.90 (18/20) | 0.80 (16/20) | 1.00 (20/20) | 0.0 / 0 | 0 |
| agent | 0.95 (19/20) | 0.85 (17/20) | 1.00 (20/20) | 3.3 / 8 | 0 |

Agent tool usage:

| Tool | calls | share |
|---|---|---|
| search_code | 22 | 33% |
| get_definition | 19 | 28% |
| expand_context | 16 | 24% |
| read_file | 9 | 13% |
| find_references | 1 | 1% |

Graph-tool use vs correctness (agent):

| Agent run | symbol-hit | symbol-miss |
|---|---|---|
| used a graph tool | 10 | 2 |
| no graph tool | 7 | 1 |

Per-question:

| q | stuffed | agent |
|---|---|---|
| q01 | ✓s | ✓s |
| q02 | ✓s | ✓s |
| q03 | ✓s | ✓s |
| q04 | ✓s | ✓s |
| q05 | ✓s | ✓s |
| q06 | ✓s | ✓s |
| q07 | ✓s | ✓s |
| q08 | ✓s | ✓s |
| q09 | ✓ | · |
| q10 | · | ✓s |
| q11 | ✓s | ✓s |
| q12 | ✓s | ✓s |
| q13 | ✓s | ✓ |
| q14 | · | ✓s |
| q15 | ✓s | ✓ |
| q16 | ✓s | ✓s |
| q17 | ✓s | ✓s |
| q18 | ✓s | ✓s |
| q19 | ✓s | ✓s |
| q20 | ✓ | ✓s |

`✓s` = file + symbol · `✓` = file only · `·` = miss


### Answer-level results — 2026-07-27

**Model:** `mistral-medium-latest` · **Set:** frozen 20 (measurement) · **Model calls:** 114 · Metric: answer-hit (≥1 validated citation whose file ∈ truth.files).

| Mode | answer-hit (file) | symbol-hit | cited | tool calls (mean/max) | errors |
|---|---|---|---|---|---|
| agent | 1.00 (20/20) | 1.00 (20/20) | 1.00 (20/20) | 4.7 / 8 | 0 |

Agent tool usage:

| Tool | calls | share |
|---|---|---|
| read_file | 54 | 55% |
| search_code | 31 | 32% |
| get_definition | 8 | 8% |
| expand_context | 5 | 5% |

Graph-tool use vs correctness (agent):

| Agent run | symbol-hit | symbol-miss |
|---|---|---|
| used a graph tool | 7 | 0 |
| no graph tool | 13 | 0 |

Per-question:

| q | agent |
|---|---|
| q01 | ✓s |
| q02 | ✓s |
| q03 | ✓s |
| q04 | ✓s |
| q05 | ✓s |
| q06 | ✓s |
| q07 | ✓s |
| q08 | ✓s |
| q09 | ✓s |
| q10 | ✓s |
| q11 | ✓s |
| q12 | ✓s |
| q13 | ✓s |
| q14 | ✓s |
| q15 | ✓s |
| q16 | ✓s |
| q17 | ✓s |
| q18 | ✓s |
| q19 | ✓s |
| q20 | ✓s |

`✓s` = file + symbol · `✓` = file only · `·` = miss


### Answer-level results — 2026-07-27

**Model:** `mistral-medium-latest` · **Set:** frozen 20 (measurement) · **Model calls:** 119 · Metric: answer-hit (≥1 validated citation whose file ∈ truth.files).

| Mode | answer-hit (file) | symbol-hit | cited | tool calls (mean/max) | errors |
|---|---|---|---|---|---|
| agent | 0.95 (19/20) | 0.95 (19/20) | 0.95 (19/20) | 5.0 / 8 | 0 |

Agent tool usage:

| Tool | calls | share |
|---|---|---|
| read_file | 58 | 56% |
| search_code | 33 | 32% |
| get_definition | 8 | 8% |
| expand_context | 4 | 4% |

Graph-tool use vs correctness (agent):

| Agent run | symbol-hit | symbol-miss |
|---|---|---|
| used a graph tool | 7 | 0 |
| no graph tool | 12 | 1 |

Per-question:

| q | agent |
|---|---|
| q01 | ✓s |
| q02 | ✓s |
| q03 | ✓s |
| q04 | ✓s |
| q05 | ✓s |
| q06 | ✓s |
| q07 | ✓s |
| q08 | ✓s |
| q09 | ✓s |
| q10 | ✓s |
| q11 | ✓s |
| q12 | · |
| q13 | ✓s |
| q14 | ✓s |
| q15 | ✓s |
| q16 | ✓s |
| q17 | ✓s |
| q18 | ✓s |
| q19 | ✓s |
| q20 | ✓s |

`✓s` = file + symbol · `✓` = file only · `·` = miss


### Answer-level results — 2026-07-27

**Model:** `mistral-medium-latest` · **Set:** frozen 20 (measurement) · **Model calls:** 112 · Metric: answer-hit (≥1 validated citation whose file ∈ truth.files).

| Mode | answer-hit (file) | symbol-hit | cited | tool calls (mean/max) | errors |
|---|---|---|---|---|---|
| agent | 0.95 (19/20) | 0.85 (17/20) | 0.95 (19/20) | 4.6 / 8 | 0 |

Agent tool usage:

| Tool | calls | share |
|---|---|---|
| read_file | 58 | 61% |
| search_code | 29 | 31% |
| expand_context | 5 | 5% |
| get_definition | 3 | 3% |

Graph-tool use vs correctness (agent):

| Agent run | symbol-hit | symbol-miss |
|---|---|---|
| used a graph tool | 6 | 0 |
| no graph tool | 11 | 3 |

Per-question:

| q | agent |
|---|---|
| q01 | ✓s |
| q02 | ✓s |
| q03 | ✓s |
| q04 | ✓s |
| q05 | ✓s |
| q06 | ✓s |
| q07 | ✓s |
| q08 | ✓s |
| q09 | ✓ |
| q10 | ✓s |
| q11 | ✓s |
| q12 | ✓s |
| q13 | ✓s |
| q14 | ✓s |
| q15 | ✓ |
| q16 | ✓s |
| q17 | · |
| q18 | ✓s |
| q19 | ✓s |
| q20 | ✓s |

`✓s` = file + symbol · `✓` = file only · `·` = miss


### Answer-level results — 2026-07-27

**Model:** `vertex:gemini-2.5-flash` · **Set:** frozen 20 (measurement) · **Model calls:** 89 · Metric: answer-hit (≥1 validated citation whose file ∈ truth.files).

| Mode | answer-hit (file) | symbol-hit | cited | tool calls (mean/max) | errors |
|---|---|---|---|---|---|
| agent | 0.95 (19/20) | 0.85 (17/20) | 1.00 (20/20) | 3.5 / 9 | 0 |

Agent tool usage:

| Tool | calls | share |
|---|---|---|
| get_definition | 24 | 32% |
| search_code | 22 | 29% |
| read_file | 17 | 23% |
| expand_context | 8 | 11% |
| find_references | 4 | 5% |

Graph-tool use vs correctness (agent):

| Agent run | symbol-hit | symbol-miss |
|---|---|---|
| used a graph tool | 9 | 3 |
| no graph tool | 8 | 0 |

Per-question:

| q | agent |
|---|---|
| q01 | ✓s |
| q02 | ✓s |
| q03 | ✓s |
| q04 | ✓s |
| q05 | ✓s |
| q06 | ✓s |
| q07 | ✓s |
| q08 | ✓s |
| q09 | ✓ |
| q10 | · |
| q11 | ✓s |
| q12 | ✓s |
| q13 | ✓s |
| q14 | ✓s |
| q15 | ✓s |
| q16 | ✓s |
| q17 | ✓s |
| q18 | ✓s |
| q19 | ✓s |
| q20 | ✓ |

`✓s` = file + symbol · `✓` = file only · `·` = miss


### Answer-level results — 2026-07-27

**Model:** `vertex:gemini-2.5-flash` · **Set:** frozen 20 (measurement) · **Model calls:** 89 · Metric: answer-hit (≥1 validated citation whose file ∈ truth.files).

| Mode | answer-hit (file) | symbol-hit | cited | tool calls (mean/max) | errors |
|---|---|---|---|---|---|
| agent | 0.95 (19/20) | 0.85 (17/20) | 1.00 (20/20) | 3.5 / 9 | 0 |

Agent tool usage:

| Tool | calls | share |
|---|---|---|
| get_definition | 24 | 32% |
| search_code | 22 | 29% |
| read_file | 17 | 23% |
| expand_context | 8 | 11% |
| find_references | 4 | 5% |

Graph-tool use vs correctness (agent):

| Agent run | symbol-hit | symbol-miss |
|---|---|---|
| used a graph tool | 9 | 3 |
| no graph tool | 8 | 0 |

Per-question:

| q | agent |
|---|---|
| q01 | ✓s |
| q02 | ✓s |
| q03 | ✓s |
| q04 | ✓s |
| q05 | ✓s |
| q06 | ✓s |
| q07 | ✓s |
| q08 | ✓s |
| q09 | ✓ |
| q10 | · |
| q11 | ✓s |
| q12 | ✓s |
| q13 | ✓s |
| q14 | ✓s |
| q15 | ✓s |
| q16 | ✓s |
| q17 | ✓s |
| q18 | ✓s |
| q19 | ✓s |
| q20 | ✓ |

`✓s` = file + symbol · `✓` = file only · `·` = miss


### Answer-level results — 2026-07-27

**Model:** `vertex:gemini-2.5-flash` · **Set:** frozen 20 (measurement) · **Model calls:** 94 · Metric: answer-hit (≥1 validated citation whose file ∈ truth.files).

| Mode | answer-hit (file) | symbol-hit | cited | tool calls (mean/max) | errors |
|---|---|---|---|---|---|
| agent | 0.95 (19/20) | 0.90 (18/20) | 1.00 (20/20) | 3.7 / 9 | 0 |

Agent tool usage:

| Tool | calls | share |
|---|---|---|
| get_definition | 25 | 31% |
| search_code | 22 | 28% |
| read_file | 18 | 22% |
| expand_context | 11 | 14% |
| find_references | 4 | 5% |

Graph-tool use vs correctness (agent):

| Agent run | symbol-hit | symbol-miss |
|---|---|---|
| used a graph tool | 10 | 2 |
| no graph tool | 8 | 0 |

Per-question:

| q | agent |
|---|---|
| q01 | ✓s |
| q02 | ✓s |
| q03 | ✓s |
| q04 | ✓s |
| q05 | ✓s |
| q06 | ✓s |
| q07 | ✓s |
| q08 | ✓s |
| q09 | ✓ |
| q10 | · |
| q11 | ✓s |
| q12 | ✓s |
| q13 | ✓s |
| q14 | ✓s |
| q15 | ✓s |
| q16 | ✓s |
| q17 | ✓s |
| q18 | ✓s |
| q19 | ✓s |
| q20 | ✓s |

`✓s` = file + symbol · `✓` = file only · `·` = miss


### Results — 2026-07-27

**Repo:** https://github.com/encode/httpx @ `b5addb64f016` — 1522 chunks (825 implementation, 697 test), 20 questions. Modes: vector, fts, hybrid, hybrid+rerank.

**Corpus condition:** implementation-only (default, is_test excluded)

| Mode | hit@3 | hit@5 | hit@10 | MRR |
|---|---|---|---|---|
| vector | 0.75 (15/20) | 0.85 (17/20) | 0.90 (18/20) | 0.722 |
| fts | 0.60 (12/20) | 0.70 (14/20) | 0.80 (16/20) | 0.503 |
| hybrid | 0.80 (16/20) | 0.85 (17/20) | 0.95 (19/20) | 0.752 |
| hybrid+rerank | 0.80 (16/20) | 0.80 (16/20) | 0.85 (17/20) | 0.722 |

Per-question hit@10:

| q | vector | fts | hybrid | hybrid+rerank |
|---|---|---|---|---|
| q01 | ✓ | · | ✓ | ✓ |
| q02 | ✓ | ✓ | ✓ | ✓ |
| q03 | ✓ | ✓ | ✓ | ✓ |
| q04 | ✓ | ✓ | ✓ | ✓ |
| q05 | ✓ | ✓ | ✓ | ✓ |
| q06 | ✓ | ✓ | ✓ | ✓ |
| q07 | ✓ | ✓ | ✓ | ✓ |
| q08 | ✓ | ✓ | ✓ | ✓ |
| q09 | · | ✓ | ✓ | · |
| q10 | · | · | · | · |
| q11 | ✓ | ✓ | ✓ | ✓ |
| q12 | ✓ | ✓ | ✓ | ✓ |
| q13 | ✓ | · | ✓ | ✓ |
| q14 | ✓ | · | ✓ | · |
| q15 | ✓ | ✓ | ✓ | ✓ |
| q16 | ✓ | ✓ | ✓ | ✓ |
| q17 | ✓ | ✓ | ✓ | ✓ |
| q18 | ✓ | ✓ | ✓ | ✓ |
| q19 | ✓ | ✓ | ✓ | ✓ |
| q20 | ✓ | ✓ | ✓ | ✓ |

**Corpus condition:** shadowed (--include-tests, is_test included)

| Mode | hit@3 | hit@5 | hit@10 | MRR |
|---|---|---|---|---|
| vector | 0.65 (13/20) | 0.80 (16/20) | 0.85 (17/20) | 0.632 |
| fts | 0.25 (5/20) | 0.45 (9/20) | 0.60 (12/20) | 0.267 |
| hybrid | 0.70 (14/20) | 0.80 (16/20) | 0.80 (16/20) | 0.617 |
| hybrid+rerank | 0.70 (14/20) | 0.75 (15/20) | 0.75 (15/20) | 0.604 |

Per-question hit@10:

| q | vector | fts | hybrid | hybrid+rerank |
|---|---|---|---|---|
| q01 | ✓ | · | ✓ | ✓ |
| q02 | ✓ | ✓ | ✓ | ✓ |
| q03 | ✓ | ✓ | ✓ | ✓ |
| q04 | ✓ | ✓ | ✓ | ✓ |
| q05 | ✓ | ✓ | ✓ | ✓ |
| q06 | ✓ | ✓ | ✓ | ✓ |
| q07 | ✓ | ✓ | ✓ | ✓ |
| q08 | ✓ | · | ✓ | · |
| q09 | · | · | · | · |
| q10 | · | · | · | · |
| q11 | ✓ | ✓ | ✓ | ✓ |
| q12 | ✓ | · | ✓ | ✓ |
| q13 | ✓ | · | ✓ | ✓ |
| q14 | ✓ | · | · | · |
| q15 | · | · | · | · |
| q16 | ✓ | ✓ | ✓ | ✓ |
| q17 | ✓ | ✓ | ✓ | ✓ |
| q18 | ✓ | ✓ | ✓ | ✓ |
| q19 | ✓ | ✓ | ✓ | ✓ |
| q20 | ✓ | ✓ | ✓ | ✓ |


### Results — 2026-07-27

**Repo:** https://github.com/encode/httpx @ `b5addb64f016` — 657 chunks (327 implementation, 330 test), 20 questions. Modes: vector, fts, hybrid, hybrid+rerank.

**Corpus condition:** implementation-only (default, is_test excluded)

| Mode | hit@3 | hit@5 | hit@10 | MRR |
|---|---|---|---|---|
| vector | 0.90 (18/20) | 0.90 (18/20) | 0.90 (18/20) | 0.767 |
| fts | 0.75 (15/20) | 0.80 (16/20) | 0.90 (18/20) | 0.647 |
| hybrid | 0.80 (16/20) | 0.80 (16/20) | 0.95 (19/20) | 0.734 |
| hybrid+rerank | 0.75 (15/20) | 0.80 (16/20) | 0.90 (18/20) | 0.684 |

Per-question hit@10:

| q | vector | fts | hybrid | hybrid+rerank |
|---|---|---|---|---|
| q01 | ✓ | ✓ | ✓ | · |
| q02 | ✓ | ✓ | ✓ | ✓ |
| q03 | ✓ | ✓ | ✓ | ✓ |
| q04 | ✓ | ✓ | ✓ | ✓ |
| q05 | ✓ | ✓ | ✓ | ✓ |
| q06 | ✓ | ✓ | ✓ | ✓ |
| q07 | ✓ | ✓ | ✓ | ✓ |
| q08 | ✓ | ✓ | ✓ | ✓ |
| q09 | · | ✓ | ✓ | ✓ |
| q10 | · | · | · | · |
| q11 | ✓ | ✓ | ✓ | ✓ |
| q12 | ✓ | ✓ | ✓ | ✓ |
| q13 | ✓ | ✓ | ✓ | ✓ |
| q14 | ✓ | · | ✓ | ✓ |
| q15 | ✓ | ✓ | ✓ | ✓ |
| q16 | ✓ | ✓ | ✓ | ✓ |
| q17 | ✓ | ✓ | ✓ | ✓ |
| q18 | ✓ | ✓ | ✓ | ✓ |
| q19 | ✓ | ✓ | ✓ | ✓ |
| q20 | ✓ | ✓ | ✓ | ✓ |


### Results — 2026-07-27

**Repo:** https://github.com/encode/httpx @ `b5addb64f016` — 657 chunks (327 implementation, 330 test), 20 questions. Modes: vector, fts, hybrid.

**Corpus condition:** implementation-only (default, is_test excluded)

| Mode | hit@3 | hit@5 | hit@10 | MRR |
|---|---|---|---|---|
| vector | 0.90 (18/20) | 0.90 (18/20) | 0.90 (18/20) | 0.767 |
| fts | 0.75 (15/20) | 0.80 (16/20) | 0.90 (18/20) | 0.647 |
| hybrid | 0.80 (16/20) | 0.80 (16/20) | 0.95 (19/20) | 0.734 |

Per-question hit@10:

| q | vector | fts | hybrid |
|---|---|---|---|
| q01 | ✓ | ✓ | ✓ |
| q02 | ✓ | ✓ | ✓ |
| q03 | ✓ | ✓ | ✓ |
| q04 | ✓ | ✓ | ✓ |
| q05 | ✓ | ✓ | ✓ |
| q06 | ✓ | ✓ | ✓ |
| q07 | ✓ | ✓ | ✓ |
| q08 | ✓ | ✓ | ✓ |
| q09 | · | ✓ | ✓ |
| q10 | · | · | · |
| q11 | ✓ | ✓ | ✓ |
| q12 | ✓ | ✓ | ✓ |
| q13 | ✓ | ✓ | ✓ |
| q14 | ✓ | · | ✓ |
| q15 | ✓ | ✓ | ✓ |
| q16 | ✓ | ✓ | ✓ |
| q17 | ✓ | ✓ | ✓ |
| q18 | ✓ | ✓ | ✓ |
| q19 | ✓ | ✓ | ✓ |
| q20 | ✓ | ✓ | ✓ |


> **The four 2026-07-29 blocks below are one experiment, not four measurements.**
> Read them in order (DECISIONS 2026-07-29):
>
> 1. **After the rendered-span fix.** Verifies `005_rendered_chunk_spans.sql`
>    moved no hit@k. It didn't — but fts MRR shifted 0.503 → 0.494, because the
>    migration's `UPDATE` rewrote row versions and the ordering had no
>    tiebreaker, so tied rows came back in a new physical order.
> 2. **After adding `id` as a tiebreaker** to every ordering in `hybrid.py`.
>    This is the deliberate, one-time re-measurement that change entails. fts
>    hit@3 0.60 → 0.55 and MRR → 0.463; hybrid hit@5 0.85 → 0.90, MRR → 0.753.
>    Both land back on the **2026-07-26 published values** — that corpus was
>    freshly ingested, so heap order was still id order, which is exactly what
>    the tiebreaker now pins permanently.
> 3. **Repeat run, nothing changed.** Byte-identical to 2.
> 4. **Repeat run after `UPDATE chunks SET part = part` on all 1522 rows** — the
>    exact mechanism that caused the drift in 1, at 14x the scale.
>    Byte-identical to 2 and 3.
>
> Blocks 2-4 being identical *is* the result: retrieval order no longer depends
> on physical row layout. Pinned by
> `test_retrieval_order_survives_a_row_rewrite`.

### Results — 2026-07-29

**Repo:** https://github.com/encode/httpx @ `b5addb64f016` — 1522 chunks (825 implementation, 697 test), 20 questions. Modes: vector, fts, hybrid.

**Corpus condition:** implementation-only (default, is_test excluded)

| Mode | hit@3 | hit@5 | hit@10 | MRR |
|---|---|---|---|---|
| vector | 0.75 (15/20) | 0.85 (17/20) | 0.90 (18/20) | 0.722 |
| fts | 0.60 (12/20) | 0.70 (14/20) | 0.80 (16/20) | 0.494 |
| hybrid | 0.80 (16/20) | 0.85 (17/20) | 0.95 (19/20) | 0.752 |

Per-question hit@10:

| q | vector | fts | hybrid |
|---|---|---|---|
| q01 | ✓ | · | ✓ |
| q02 | ✓ | ✓ | ✓ |
| q03 | ✓ | ✓ | ✓ |
| q04 | ✓ | ✓ | ✓ |
| q05 | ✓ | ✓ | ✓ |
| q06 | ✓ | ✓ | ✓ |
| q07 | ✓ | ✓ | ✓ |
| q08 | ✓ | ✓ | ✓ |
| q09 | · | ✓ | ✓ |
| q10 | · | · | · |
| q11 | ✓ | ✓ | ✓ |
| q12 | ✓ | ✓ | ✓ |
| q13 | ✓ | · | ✓ |
| q14 | ✓ | · | ✓ |
| q15 | ✓ | ✓ | ✓ |
| q16 | ✓ | ✓ | ✓ |
| q17 | ✓ | ✓ | ✓ |
| q18 | ✓ | ✓ | ✓ |
| q19 | ✓ | ✓ | ✓ |
| q20 | ✓ | ✓ | ✓ |


### Results — 2026-07-29

**Repo:** https://github.com/encode/httpx @ `b5addb64f016` — 1522 chunks (825 implementation, 697 test), 20 questions. Modes: vector, fts, hybrid.

**Corpus condition:** implementation-only (default, is_test excluded)

| Mode | hit@3 | hit@5 | hit@10 | MRR |
|---|---|---|---|---|
| vector | 0.75 (15/20) | 0.85 (17/20) | 0.90 (18/20) | 0.722 |
| fts | 0.55 (11/20) | 0.70 (14/20) | 0.80 (16/20) | 0.463 |
| hybrid | 0.80 (16/20) | 0.90 (18/20) | 0.95 (19/20) | 0.753 |

Per-question hit@10:

| q | vector | fts | hybrid |
|---|---|---|---|
| q01 | ✓ | · | ✓ |
| q02 | ✓ | ✓ | ✓ |
| q03 | ✓ | ✓ | ✓ |
| q04 | ✓ | ✓ | ✓ |
| q05 | ✓ | ✓ | ✓ |
| q06 | ✓ | ✓ | ✓ |
| q07 | ✓ | ✓ | ✓ |
| q08 | ✓ | ✓ | ✓ |
| q09 | · | ✓ | ✓ |
| q10 | · | · | · |
| q11 | ✓ | ✓ | ✓ |
| q12 | ✓ | ✓ | ✓ |
| q13 | ✓ | · | ✓ |
| q14 | ✓ | · | ✓ |
| q15 | ✓ | ✓ | ✓ |
| q16 | ✓ | ✓ | ✓ |
| q17 | ✓ | ✓ | ✓ |
| q18 | ✓ | ✓ | ✓ |
| q19 | ✓ | ✓ | ✓ |
| q20 | ✓ | ✓ | ✓ |


### Results — 2026-07-29

**Repo:** https://github.com/encode/httpx @ `b5addb64f016` — 1522 chunks (825 implementation, 697 test), 20 questions. Modes: vector, fts, hybrid.

**Corpus condition:** implementation-only (default, is_test excluded)

| Mode | hit@3 | hit@5 | hit@10 | MRR |
|---|---|---|---|---|
| vector | 0.75 (15/20) | 0.85 (17/20) | 0.90 (18/20) | 0.722 |
| fts | 0.55 (11/20) | 0.70 (14/20) | 0.80 (16/20) | 0.463 |
| hybrid | 0.80 (16/20) | 0.90 (18/20) | 0.95 (19/20) | 0.753 |

Per-question hit@10:

| q | vector | fts | hybrid |
|---|---|---|---|
| q01 | ✓ | · | ✓ |
| q02 | ✓ | ✓ | ✓ |
| q03 | ✓ | ✓ | ✓ |
| q04 | ✓ | ✓ | ✓ |
| q05 | ✓ | ✓ | ✓ |
| q06 | ✓ | ✓ | ✓ |
| q07 | ✓ | ✓ | ✓ |
| q08 | ✓ | ✓ | ✓ |
| q09 | · | ✓ | ✓ |
| q10 | · | · | · |
| q11 | ✓ | ✓ | ✓ |
| q12 | ✓ | ✓ | ✓ |
| q13 | ✓ | · | ✓ |
| q14 | ✓ | · | ✓ |
| q15 | ✓ | ✓ | ✓ |
| q16 | ✓ | ✓ | ✓ |
| q17 | ✓ | ✓ | ✓ |
| q18 | ✓ | ✓ | ✓ |
| q19 | ✓ | ✓ | ✓ |
| q20 | ✓ | ✓ | ✓ |


### Results — 2026-07-29

**Repo:** https://github.com/encode/httpx @ `b5addb64f016` — 1522 chunks (825 implementation, 697 test), 20 questions. Modes: vector, fts, hybrid.

**Corpus condition:** implementation-only (default, is_test excluded)

| Mode | hit@3 | hit@5 | hit@10 | MRR |
|---|---|---|---|---|
| vector | 0.75 (15/20) | 0.85 (17/20) | 0.90 (18/20) | 0.722 |
| fts | 0.55 (11/20) | 0.70 (14/20) | 0.80 (16/20) | 0.463 |
| hybrid | 0.80 (16/20) | 0.90 (18/20) | 0.95 (19/20) | 0.753 |

Per-question hit@10:

| q | vector | fts | hybrid |
|---|---|---|---|
| q01 | ✓ | · | ✓ |
| q02 | ✓ | ✓ | ✓ |
| q03 | ✓ | ✓ | ✓ |
| q04 | ✓ | ✓ | ✓ |
| q05 | ✓ | ✓ | ✓ |
| q06 | ✓ | ✓ | ✓ |
| q07 | ✓ | ✓ | ✓ |
| q08 | ✓ | ✓ | ✓ |
| q09 | · | ✓ | ✓ |
| q10 | · | · | · |
| q11 | ✓ | ✓ | ✓ |
| q12 | ✓ | ✓ | ✓ |
| q13 | ✓ | · | ✓ |
| q14 | ✓ | · | ✓ |
| q15 | ✓ | ✓ | ✓ |
| q16 | ✓ | ✓ | ✓ |
| q17 | ✓ | ✓ | ✓ |
| q18 | ✓ | ✓ | ✓ |
| q19 | ✓ | ✓ | ✓ |
| q20 | ✓ | ✓ | ✓ |


### Results — 2026-07-30

**Repo:** https://github.com/encode/httpx @ `b5addb64f016` — 1522 chunks (825 implementation, 697 test), 20 questions. Modes: vector, fts, hybrid.

**Corpus condition:** implementation-only (default, is_test excluded)

| Mode | hit@3 | hit@5 | hit@10 | MRR |
|---|---|---|---|---|
| vector | 0.75 (15/20) | 0.85 (17/20) | 0.90 (18/20) | 0.722 |
| fts | 0.55 (11/20) | 0.70 (14/20) | 0.80 (16/20) | 0.463 |
| hybrid | 0.80 (16/20) | 0.90 (18/20) | 0.95 (19/20) | 0.753 |

Per-question hit@10:

| q | vector | fts | hybrid |
|---|---|---|---|
| q01 | ✓ | · | ✓ |
| q02 | ✓ | ✓ | ✓ |
| q03 | ✓ | ✓ | ✓ |
| q04 | ✓ | ✓ | ✓ |
| q05 | ✓ | ✓ | ✓ |
| q06 | ✓ | ✓ | ✓ |
| q07 | ✓ | ✓ | ✓ |
| q08 | ✓ | ✓ | ✓ |
| q09 | · | ✓ | ✓ |
| q10 | · | · | · |
| q11 | ✓ | ✓ | ✓ |
| q12 | ✓ | ✓ | ✓ |
| q13 | ✓ | · | ✓ |
| q14 | ✓ | · | ✓ |
| q15 | ✓ | ✓ | ✓ |
| q16 | ✓ | ✓ | ✓ |
| q17 | ✓ | ✓ | ✓ |
| q18 | ✓ | ✓ | ✓ |
| q19 | ✓ | ✓ | ✓ |
| q20 | ✓ | ✓ | ✓ |


### Results — 2026-07-30

**Repo:** https://github.com/encode/httpx @ `b5addb64f016` — 1522 chunks (825 implementation, 697 test), 20 questions. Modes: vector, fts, hybrid.

**Corpus condition:** implementation-only (default, is_test excluded)

| Mode | hit@3 | hit@5 | hit@10 | MRR |
|---|---|---|---|---|
| vector | 0.75 (15/20) | 0.85 (17/20) | 0.90 (18/20) | 0.722 |
| fts | 0.55 (11/20) | 0.70 (14/20) | 0.80 (16/20) | 0.463 |
| hybrid | 0.80 (16/20) | 0.90 (18/20) | 0.95 (19/20) | 0.753 |

Per-question hit@10:

| q | vector | fts | hybrid |
|---|---|---|---|
| q01 | ✓ | · | ✓ |
| q02 | ✓ | ✓ | ✓ |
| q03 | ✓ | ✓ | ✓ |
| q04 | ✓ | ✓ | ✓ |
| q05 | ✓ | ✓ | ✓ |
| q06 | ✓ | ✓ | ✓ |
| q07 | ✓ | ✓ | ✓ |
| q08 | ✓ | ✓ | ✓ |
| q09 | · | ✓ | ✓ |
| q10 | · | · | · |
| q11 | ✓ | ✓ | ✓ |
| q12 | ✓ | ✓ | ✓ |
| q13 | ✓ | · | ✓ |
| q14 | ✓ | · | ✓ |
| q15 | ✓ | ✓ | ✓ |
| q16 | ✓ | ✓ | ✓ |
| q17 | ✓ | ✓ | ✓ |
| q18 | ✓ | ✓ | ✓ |
| q19 | ✓ | ✓ | ✓ |
| q20 | ✓ | ✓ | ✓ |


### Results — 2026-07-30

**Repo:** https://github.com/encode/httpx @ `b5addb64f016` — 657 chunks (327 implementation, 330 test), 20 questions. Modes: vector, fts, hybrid.

**Corpus condition:** implementation-only (default, is_test excluded)

| Mode | hit@3 | hit@5 | hit@10 | MRR |
|---|---|---|---|---|
| vector | 0.90 (18/20) | 0.90 (18/20) | 0.90 (18/20) | 0.767 |
| fts | 0.75 (15/20) | 0.80 (16/20) | 0.90 (18/20) | 0.647 |
| hybrid | 0.80 (16/20) | 0.80 (16/20) | 0.95 (19/20) | 0.759 |

Per-question hit@10:

| q | vector | fts | hybrid |
|---|---|---|---|
| q01 | ✓ | ✓ | ✓ |
| q02 | ✓ | ✓ | ✓ |
| q03 | ✓ | ✓ | ✓ |
| q04 | ✓ | ✓ | ✓ |
| q05 | ✓ | ✓ | ✓ |
| q06 | ✓ | ✓ | ✓ |
| q07 | ✓ | ✓ | ✓ |
| q08 | ✓ | ✓ | ✓ |
| q09 | · | ✓ | ✓ |
| q10 | · | · | · |
| q11 | ✓ | ✓ | ✓ |
| q12 | ✓ | ✓ | ✓ |
| q13 | ✓ | ✓ | ✓ |
| q14 | ✓ | · | ✓ |
| q15 | ✓ | ✓ | ✓ |
| q16 | ✓ | ✓ | ✓ |
| q17 | ✓ | ✓ | ✓ |
| q18 | ✓ | ✓ | ✓ |
| q19 | ✓ | ✓ | ✓ |
| q20 | ✓ | ✓ | ✓ |


### Results — 2026-07-30

**Repo:** https://github.com/encode/httpx @ `b5addb64f016` — 1522 chunks (825 implementation, 697 test), 20 questions. Modes: vector, fts, hybrid.

**Corpus condition:** implementation-only (default, is_test excluded)

| Mode | hit@3 | hit@5 | hit@10 | MRR |
|---|---|---|---|---|
| vector | 0.75 (15/20) | 0.85 (17/20) | 0.90 (18/20) | 0.722 |
| fts | 0.55 (11/20) | 0.70 (14/20) | 0.80 (16/20) | 0.463 |
| hybrid | 0.80 (16/20) | 0.90 (18/20) | 0.95 (19/20) | 0.753 |

Per-question hit@10:

| q | vector | fts | hybrid |
|---|---|---|---|
| q01 | ✓ | · | ✓ |
| q02 | ✓ | ✓ | ✓ |
| q03 | ✓ | ✓ | ✓ |
| q04 | ✓ | ✓ | ✓ |
| q05 | ✓ | ✓ | ✓ |
| q06 | ✓ | ✓ | ✓ |
| q07 | ✓ | ✓ | ✓ |
| q08 | ✓ | ✓ | ✓ |
| q09 | · | ✓ | ✓ |
| q10 | · | · | · |
| q11 | ✓ | ✓ | ✓ |
| q12 | ✓ | ✓ | ✓ |
| q13 | ✓ | · | ✓ |
| q14 | ✓ | · | ✓ |
| q15 | ✓ | ✓ | ✓ |
| q16 | ✓ | ✓ | ✓ |
| q17 | ✓ | ✓ | ✓ |
| q18 | ✓ | ✓ | ✓ |
| q19 | ✓ | ✓ | ✓ |
| q20 | ✓ | ✓ | ✓ |


### Results — 2026-07-30

**Repo:** https://github.com/encode/httpx @ `b5addb64f016` — 1522 chunks (825 implementation, 697 test), 20 questions. Modes: hybrid.

**Corpus condition:** implementation-only (default, is_test excluded)

| Mode | hit@3 | hit@5 | hit@10 | MRR |
|---|---|---|---|---|
| hybrid | 0.80 (16/20) | 0.90 (18/20) | 0.95 (19/20) | 0.753 |

Per-question hit@10:

| q | hybrid |
|---|---|
| q01 | ✓ |
| q02 | ✓ |
| q03 | ✓ |
| q04 | ✓ |
| q05 | ✓ |
| q06 | ✓ |
| q07 | ✓ |
| q08 | ✓ |
| q09 | ✓ |
| q10 | · |
| q11 | ✓ |
| q12 | ✓ |
| q13 | ✓ |
| q14 | ✓ |
| q15 | ✓ |
| q16 | ✓ |
| q17 | ✓ |
| q18 | ✓ |
| q19 | ✓ |
| q20 | ✓ |


### Results — 2026-07-30

**Repo:** https://github.com/encode/httpx @ `b5addb64f016` — 1522 chunks (825 implementation, 697 test), 20 questions. Modes: vector, fts, hybrid.

**Corpus condition:** implementation-only (default, is_test excluded)

| Mode | hit@3 | hit@5 | hit@10 | MRR |
|---|---|---|---|---|
| vector | 0.75 (15/20) | 0.85 (17/20) | 0.90 (18/20) | 0.722 |
| fts | 0.55 (11/20) | 0.70 (14/20) | 0.80 (16/20) | 0.463 |
| hybrid | 0.80 (16/20) | 0.90 (18/20) | 0.95 (19/20) | 0.753 |

Per-question hit@10:

| q | vector | fts | hybrid |
|---|---|---|---|
| q01 | ✓ | · | ✓ |
| q02 | ✓ | ✓ | ✓ |
| q03 | ✓ | ✓ | ✓ |
| q04 | ✓ | ✓ | ✓ |
| q05 | ✓ | ✓ | ✓ |
| q06 | ✓ | ✓ | ✓ |
| q07 | ✓ | ✓ | ✓ |
| q08 | ✓ | ✓ | ✓ |
| q09 | · | ✓ | ✓ |
| q10 | · | · | · |
| q11 | ✓ | ✓ | ✓ |
| q12 | ✓ | ✓ | ✓ |
| q13 | ✓ | · | ✓ |
| q14 | ✓ | · | ✓ |
| q15 | ✓ | ✓ | ✓ |
| q16 | ✓ | ✓ | ✓ |
| q17 | ✓ | ✓ | ✓ |
| q18 | ✓ | ✓ | ✓ |
| q19 | ✓ | ✓ | ✓ |
| q20 | ✓ | ✓ | ✓ |


### Results — 2026-08-01

**Repo:** https://github.com/encode/httpx @ `b5addb64f016` — 1522 chunks (825 implementation, 697 test), 20 questions. Modes: fts.

**Corpus condition:** implementation-only (default, is_test excluded)

| Mode | hit@3 | hit@5 | hit@10 | MRR |
|---|---|---|---|---|
| fts | 0.55 (11/20) | 0.70 (14/20) | 0.80 (16/20) | 0.463 |

Per-question hit@10:

| q | fts |
|---|---|
| q01 | · |
| q02 | ✓ |
| q03 | ✓ |
| q04 | ✓ |
| q05 | ✓ |
| q06 | ✓ |
| q07 | ✓ |
| q08 | ✓ |
| q09 | ✓ |
| q10 | · |
| q11 | ✓ |
| q12 | ✓ |
| q13 | · |
| q14 | · |
| q15 | ✓ |
| q16 | ✓ |
| q17 | ✓ |
| q18 | ✓ |
| q19 | ✓ |
| q20 | ✓ |

