# EVAL-FLASK.md — second frozen benchmark (replication)

**Benchmark repo:** [`pallets/flask`](https://github.com/pallets/flask)
**Pinned commit:** `6a2f545bfd8ed31e19066a299296917e034aca58` (branch `main`)

A second, independent benchmark, added **2026-08-01** to test whether the
results measured on `encode/httpx` are properties of the *system* or of *that
repo*. Everything about the first benchmark — one corpus, twenty questions, two
model families — leaves that question open, and the naive-chunking result in
particular (see below) is the kind of finding that most needs a second look.

## Why this repo, and why it was not chosen after seeing anything

`pallets/flask` is **pre-registered**: `ROADMAP.md` Phase 1 names exactly two
candidate benchmark repos — *"candidates: `encode/httpx`, `pallets/flask`"* —
written before any retrieval code existed. Taking the other name on that list is
the one choice that cannot be an accusation of picking a repo that flatters the
result.

It is also usefully *different in shape* from httpx, which matters more than
being similar: httpx is a flat client library whose modules are mostly peers,
while flask is a framework with a `sansio/` core, a plugin-ish `json/` package,
class-based views, a CLI, and a template layer. A finding that holds across both
shapes is worth more than one that holds twice on the same shape.

## Rules

Identical to `EVAL.md`, and they bind the same way:

- **Written blind.** These twenty questions and their ground truth were written
  on 2026-08-01 by reading the repository at the pinned SHA — file listings and
  `class`/`def` structure — **before this repo was ingested and before a single
  retrieval query was run against it.** No question was adjusted after seeing a
  score.
- **Frozen on first `scripts/eval.py` run.** Do not add, remove, or reword a
  question or its ground truth afterwards. Fix retrieval, not the benchmark.
- **File paths** are relative to the repo root and pinned to the SHA above.
  `truth.files` is **any-of**; at least one file per question. It is
  authoritative.
- **Symbols** are short names, matched when a chunk's dotted `qualname` equals
  the entry **or ends with `"." + entry`**. Advisory, not authoritative.
- **Metrics** exactly as SPEC §11.2. Results are appended as dated blocks by
  `scripts/eval.py --benchmark docs/EVAL-FLASK.md`. **Never edit an old block.**

## Question design

The same three tiers and the same intent as `EVAL.md`, so the two benchmarks are
comparable rather than merely both present:

- **locate (q01–q07)** — deliberately easy; the target identifier appears in the
  question.
- **conceptual (q08–q15)** — "how does X work", phrased in the vocabulary of
  someone who does not yet know the codebase's names.
- **flow (q16–q20)** — "what happens when…", spanning more than one function.

Conceptual and flow questions are written to avoid handing the retriever the
answer's identifiers, which is what makes the benchmark discriminate between
lexical matching and semantic retrieval. Stoplist for that check, adapted from
EVAL.md's to this repo: `{flask, app, application, request, response, http,
web}`.

**Measured, not asserted:** **10 of 20** questions share no identifier token with
any symbol in their own ground truth (tokenised, snake_case/CamelCase-split,
stoplisted). EVAL.md's httpx set measures 11 of 20 by the same rule, so the two
benchmarks are comparably hard on the property that matters — a set where the
question always contains the answer's name would flatter lexical retrieval and
tell us nothing.

Ground truth was checked mechanically before the first run: every `truth.files`
path exists at the pinned SHA, and every `truth.symbols` entry is actually
defined by a `class` or `def` in one of that question's truth files. Zero errors.

## Questions

```yaml
- id: q01
  tier: locate
  question: "Where is the Config class that loads application settings defined?"
  truth:
    files: ["src/flask/config.py"]
    symbols: ["Config"]

- id: q02
  tier: locate
  question: "Where is the Blueprint class defined?"
  truth:
    files: ["src/flask/sansio/blueprints.py"]
    symbols: ["Blueprint"]

- id: q03
  tier: locate
  question: "Where is SecureCookieSessionInterface defined?"
  truth:
    files: ["src/flask/sessions.py"]
    symbols: ["SecureCookieSessionInterface"]

- id: q04
  tier: locate
  question: "Where is the MethodView class for class-based views defined?"
  truth:
    files: ["src/flask/views.py"]
    symbols: ["MethodView", "View"]

- id: q05
  tier: locate
  question: "Where is send_file implemented?"
  truth:
    files: ["src/flask/helpers.py"]
    symbols: ["send_file", "send_from_directory"]

- id: q06
  tier: locate
  question: "Where is DefaultJSONProvider defined?"
  truth:
    files: ["src/flask/json/provider.py"]
    symbols: ["DefaultJSONProvider", "JSONProvider"]

- id: q07
  tier: locate
  question: "Where is the AppContext class defined?"
  truth:
    files: ["src/flask/ctx.py"]
    symbols: ["AppContext"]

- id: q08
  tier: conceptual
  question: "How does an incoming web address get matched to the function that handles it?"
  truth:
    files: ["src/flask/app.py", "src/flask/ctx.py"]
    symbols: ["create_url_adapter", "match_request", "dispatch_request"]

- id: q09
  tier: conceptual
  question: "How is a visitor's state remembered between visits without storing anything on the server?"
  truth:
    files: ["src/flask/sessions.py"]
    symbols: ["SecureCookieSessionInterface", "open_session", "save_session"]

- id: q10
  tier: conceptual
  question: "How can settings be supplied through operating-system environment variables?"
  truth:
    files: ["src/flask/config.py"]
    symbols: ["from_prefixed_env", "from_envvar"]

- id: q11
  tier: conceptual
  question: "How is an HTML page file located and loaded for rendering?"
  truth:
    files: ["src/flask/templating.py"]
    symbols: ["DispatchingJinjaLoader", "render_template"]

- id: q12
  tier: conceptual
  question: "What converts the value a handler function returns into a complete reply object?"
  truth:
    files: ["src/flask/app.py"]
    symbols: ["make_response", "finalize_request"]

- id: q13
  tier: conceptual
  question: "How is it decided whether a cookie needs to be sent back to the browser?"
  truth:
    files: ["src/flask/sessions.py"]
    symbols: ["should_set_cookie", "get_expiration_time"]

- id: q14
  tier: conceptual
  question: "How are non-primitive Python values preserved when data is signed and stored in a cookie?"
  truth:
    files: ["src/flask/json/tag.py"]
    symbols: ["TaggedJSONSerializer", "JSONTag"]

- id: q15
  tier: conceptual
  question: "How is the logger for an application created and configured?"
  truth:
    files: ["src/flask/logging.py"]
    symbols: ["create_logger", "has_level_handler"]

- id: q16
  tier: flow
  question: "What happens between a request arriving and a reply being returned?"
  truth:
    files: ["src/flask/app.py"]
    symbols: ["full_dispatch_request", "dispatch_request", "finalize_request"]

- id: q17
  tier: flow
  question: "What happens when code in a handler raises an error that nothing catches?"
  truth:
    files: ["src/flask/app.py"]
    symbols: ["handle_user_exception", "handle_exception", "log_exception"]

- id: q18
  tier: flow
  question: "What happens when the built-in development server is started from a terminal?"
  truth:
    files: ["src/flask/cli.py", "src/flask/app.py"]
    symbols: ["run", "ScriptInfo", "locate_app"]

- id: q19
  tier: flow
  question: "What happens when output is produced lazily and needs the surrounding context to stay alive?"
  truth:
    files: ["src/flask/helpers.py"]
    symbols: ["stream_with_context"]

- id: q20
  tier: flow
  question: "What happens when two handlers are registered under the same name?"
  truth:
    files: ["src/flask/sansio/app.py", "src/flask/sansio/blueprints.py"]
    symbols: ["add_url_rule", "register"]
```

### Results — 2026-08-01

**Repo:** https://github.com/pallets/flask @ `6a2f545bfd8e` — 1653 chunks (1059 implementation, 594 test), 20 questions. Modes: vector, fts, hybrid.

**Corpus condition:** implementation-only (default, is_test excluded)

| Mode | hit@3 | hit@5 | hit@10 | MRR |
|---|---|---|---|---|
| vector | 0.85 (17/20) | 0.95 (19/20) | 0.95 (19/20) | 0.837 |
| fts | 0.60 (12/20) | 0.90 (18/20) | 0.95 (19/20) | 0.623 |
| hybrid | 0.75 (15/20) | 0.90 (18/20) | 0.95 (19/20) | 0.767 |

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
| q09 | ✓ | · | ✓ |
| q10 | ✓ | ✓ | ✓ |
| q11 | ✓ | ✓ | ✓ |
| q12 | ✓ | ✓ | ✓ |
| q13 | ✓ | ✓ | ✓ |
| q14 | · | ✓ | · |
| q15 | ✓ | ✓ | ✓ |
| q16 | ✓ | ✓ | ✓ |
| q17 | ✓ | ✓ | ✓ |
| q18 | ✓ | ✓ | ✓ |
| q19 | ✓ | ✓ | ✓ |
| q20 | ✓ | ✓ | ✓ |


### Results — 2026-08-01

**Repo:** https://github.com/pallets/flask @ `6a2f545bfd8e` — 710 chunks (429 implementation, 281 test), 20 questions. Modes: vector, fts, hybrid.

**Corpus condition:** implementation-only (default, is_test excluded)

| Mode | hit@3 | hit@5 | hit@10 | MRR |
|---|---|---|---|---|
| vector | 0.90 (18/20) | 0.90 (18/20) | 0.95 (19/20) | 0.781 |
| fts | 0.55 (11/20) | 0.75 (15/20) | 0.80 (16/20) | 0.578 |
| hybrid | 0.80 (16/20) | 0.85 (17/20) | 0.90 (18/20) | 0.720 |

Per-question hit@10:

| q | vector | fts | hybrid |
|---|---|---|---|
| q01 | ✓ | ✓ | ✓ |
| q02 | ✓ | ✓ | ✓ |
| q03 | ✓ | · | ✓ |
| q04 | ✓ | ✓ | ✓ |
| q05 | ✓ | ✓ | ✓ |
| q06 | ✓ | · | ✓ |
| q07 | ✓ | ✓ | ✓ |
| q08 | ✓ | ✓ | ✓ |
| q09 | ✓ | · | ✓ |
| q10 | ✓ | ✓ | ✓ |
| q11 | ✓ | · | · |
| q12 | ✓ | ✓ | ✓ |
| q13 | ✓ | ✓ | ✓ |
| q14 | · | ✓ | · |
| q15 | ✓ | ✓ | ✓ |
| q16 | ✓ | ✓ | ✓ |
| q17 | ✓ | ✓ | ✓ |
| q18 | ✓ | ✓ | ✓ |
| q19 | ✓ | ✓ | ✓ |
| q20 | ✓ | ✓ | ✓ |


---

## Replication findings — 2026-08-01

Measured on the two dated blocks above plus the `2026-07-30` httpx blocks in
`EVAL.md`. Same code, same commit of this project, same conditions
(implementation-only). **Two of the three things the httpx numbers were read as
showing do not replicate.**

| | httpx AST | httpx naive | flask AST | flask naive |
|---|---|---|---|---|
| vector hit@3 / @5 / @10 | 0.75 / 0.85 / 0.90 | 0.90 / 0.90 / 0.90 | 0.85 / 0.95 / 0.95 | 0.90 / 0.90 / 0.95 |
| vector MRR | 0.722 | 0.767 | **0.837** | **0.781** |
| fts hit@3 / @5 / @10 | 0.55 / 0.70 / 0.80 | 0.75 / 0.80 / 0.90 | 0.60 / 0.90 / 0.95 | 0.55 / 0.75 / 0.80 |
| fts MRR | 0.463 | 0.647 | 0.623 | 0.578 |
| **hybrid** hit@3 / @5 / @10 | 0.80 / 0.90 / **0.95** | 0.80 / 0.80 / **0.95** | 0.75 / 0.90 / **0.95** | 0.80 / 0.85 / **0.90** |
| **hybrid** MRR | 0.753 | 0.759 | 0.767 | 0.720 |

### 1. "Naive chunking ties AST" — DOES NOT REPLICATE, and the sign flips

On httpx the default pipeline tied at hit@10 0.95 and naive edged ahead on MRR
(0.759 vs 0.753), which the README reports as *"naive does not lose."* On flask
the same comparison goes the other way: **AST 0.95 vs naive 0.90 at hit@10, and
0.767 vs 0.720 at MRR.**

The honest reading is not "AST wins after all". It is that **the difference is
smaller than the noise floor of a 20-question benchmark in both directions** —
one question either way — and that a result which changes sign between two repos
was never strong enough to carry the weight the README put on it. Two repos now
say: at hit@k, AST chunking and fixed windows are not reliably distinguishable.

### 2. Hybrid fusion beating every single signal — DOES NOT REPLICATE

This is the more consequential one, because it is the Phase 2 gate.

On httpx, hybrid dominated at every k and at MRR. **On flask, plain vector
search beats the shipped hybrid pipeline** — on the AST corpus at hit@3 (0.85 vs
0.75) and MRR (**0.837 vs 0.767**), and on the naive corpus at *every* k and MRR
(0.90/0.90/0.95 vs 0.80/0.85/0.90, MRR 0.781 vs 0.720).

The Phase 2 done-when — *"default retrieval pipeline hit@10 ≥ every single-signal
mode"* — still **passes on the AST corpus, by a tie** (0.95 all three). It would
have **failed** on the naive corpus (hybrid 0.90 < vector 0.95), which is not the
product and so not the gate, but is the same algorithm on the same questions.

Mechanism, stated as a hypothesis rather than a finding: RRF fuses *ranks*, so it
pulls a strong ranking toward a weaker one. httpx's lexical leg is poor
(MRR 0.463), so fusion could only add; flask's vector leg is excellent
(MRR 0.837), so fusion could only dilute. **If that is right, hybrid helps when
the lexical signal is weak and hurts when the dense signal is strong** — and
which of those a repo is cannot be known before measuring it. Testing it needs a
third repo, not more argument.

### 3. The ~20% unresolved-edge budget — DOES NOT HOLD

`SPEC §6.1` budgets ~20% unresolved edges, calibrated on httpx's measured **4%**.
Flask's symbol pass reports **52%** overall (imports 45%, calls 54%, extends
45%), which is **2.6× the budget and 13× httpx**. Edge density across every
indexed repo puts httpx alone at 1.92 edges/symbol and everything else at ≤1.07
— httpx is the outlier, not flask.

This matters more than the retrieval numbers, because the symbol graph is what
the project claims as its differentiator over plain retrieval. On flask that
graph is less than half as dense per symbol. **The cause is not diagnosed here**
— `src/`-layout packaging breaking Jedi's project root is the obvious suspect and
three of the four other src-layout repos also sit low, but the two small flat
repos sit low too, so layout is not established. It is a real open question, not
a footnote.

### What this run does NOT cover

- **Answer-level eval (agent vs stuffed) was not run on flask.** It needs ~40
  model calls against a 20-request/day tier. The Phase 3 findings (a)/(b)/(c)
  therefore remain measured on httpx only, and nothing here confirms or
  disconfirms them.
- **`hybrid+rerank` was not measured** on either flask corpus — the 2.4 GB
  cross-encoder is the same one footnoted as unrefreshed in the README.
- **n = 20 per repo.** Every difference discussed above is one or two questions.
  The value of this benchmark is that it changes *sign* on two of three claims,
  which no amount of precision on a single repo could have revealed.
