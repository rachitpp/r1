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

