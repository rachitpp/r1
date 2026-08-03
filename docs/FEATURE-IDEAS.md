# FEATURE-IDEAS.md — extending the core

> **What this is.** A catalogue of forward-looking ideas for growing what this
> project _does_ — turning "an unfamiliar repo into cited answers" into
> something bigger. This is **exploratory**, not committed work: it is a sibling
> of `ROADMAP.md` (v1 phases 0–6) and `V2.md` (multi-tenant phases V1–V5), not a
> replacement. Nothing here is scheduled; it is a menu with honest price tags.
>
> Each idea is written against the system as it actually exists today, so the
> "how it fits" notes reference real parts: the tree-sitter chunker, the Jedi
> symbol pass, the pgvector + FTS hybrid search, the 6-tool LangGraph agent, the
> SSE stream, immutable commit-pinned snapshots, and the provider-configurable
> model. Read `SPEC.md` for the contracts those refer to.

> **Status, 2026-07-31.** Seven items are **BUILT** — 2.2, 2.4, **3.1**, 3.5, 4.5,
> 6.2 and 6.6 — and are marked as such below, each with what shipped _and_ what it looks
> like to a user. (2.2 and 2.4 landed as endpoints first and were briefly "built"
> with no consumer; both now have a surface.) They were taken together because none of
> them touches ingest or retrieval, so none could disturb the eval-equality
> verification V2/V3 rest on. See SPEC §18 and DECISIONS 2026-07-31.
>
> **Status, 2026-08-02 (latest).** **4.4** built as SPEC §23 — **twelve** — which
> takes the recommended sequence through step 5. It is the first feature here
> whose cost is _recurring_: every later turn pays for the context window, which
> is why it is bounded by turns **and** by per-answer length. See DECISIONS.
>
> **Status, 2026-08-02.** **6.5** built as SPEC §22 — **eleven** — which
> closes the recommended sequence's step 4 entirely. Like 2.2 and 3.3 it costs no
> model call; unlike them, its unit tests were green while its output was wrong,
> and only running it against flask and httpx showed it. See DECISIONS.
>
> **Status, 2026-08-02.** **6.1** built as SPEC §21 — **ten**. It closes
> the doc's step 4 except for 6.5, and it is the first feature here whose hard
> part was a security boundary rather than a query: the permalink read is the
> only unauthenticated route in the API. See DECISIONS 2026-08-02.
>
> **Status, 2026-08-02 (later).** **2.1** built as SPEC §20, bringing the total
> to **nine**. Notable as the first item since the §18 batch to touch ingest: it
> needed a migration, a `git log` pass, and a change to §2.1's depth-1 clone,
> which existed _because_ history was out of scope. The catalogue's sketch called
> for a 7th agent tool; §18.1's rule said endpoint, and endpoint is what shipped.
> See DECISIONS 2026-08-02.
>
> **Status, 2026-08-02.** **3.3** built, bringing the total to eight. It is the
> second view of 2.2's rollup rather than anything new — no endpoint, no request,
> no model call — which is why it was the right thing to take next and why it took
> an afternoon. See SPEC §18.6 and DECISIONS 2026-08-02. It was also built in the
> right order by luck rather than judgement: the src-layout resolution fix landed
> the same day, and a module diagram drawn over the previous graph would have been
> a confident picture of half a repo.
>
> **The re-ingest that precondition implied is done, and was already done when
> this was written.** Audited against the database, not the notes: all five
> library corpora carry post-fix graphs matching the counts in DECISIONS
> (flask 1605, flask-sqlalchemy 311, blinker 193, itsdangerous 264, markupsafe
> 26 unchanged). httpx is exempt _by construction_ — a flat layout yields no
> import root — and its `825 | 697` invariant verifies. Only three throwaway
> `rachitpp/*` submissions from 07-28 still hold pre-fix graphs, and nothing
> reads them. So `architecture`, `coverage`, `overview` and the diagram are all
> trustworthy on every corpus that matters.
>
> **The cost model below understates one thing, and it is the important one.**
> "$0 unless noted" is true about _invoices_ and misleading about _capacity_.
> The real currency is provider rate limits: `app/agent/model.py` records that
> the AI Studio key's actual ceiling is **20 requests/day/model — two agent
> runs**, which is what forced the documented Mistral/Gemini/Vertex role split.
> So 3.1 (one cached run per snapshot) is genuinely free, while 3.2, 3.4, 4.3
> and 5.1 all _multiply_ runs against a tier that has already proven too thin
> once. Weigh those against quota, not against dollars.
>
> **Two corrections to the text below.** (1) "V1–V3 done" in _Relationship to
> the existing plans_ is optimistic: V2.md shows V1 at `[~]` (the auth'd chat
> stream is unverified on this host) and V2 with an open rollback box. (2) §2.5
> attributes the ~45% external-import figure to `SPEC §6.1`; the number is real
> but it comes from ROADMAP Phase 3's done-when, not from that section.

---

## How to read this document

Every idea uses the same template so they are comparable:

- **What it is** — one or two sentences.
- **Why it matters** — the user problem it solves.
- **How it fits the current architecture** — what it reuses vs. what is new.
- **Implementation sketch** — the concrete steps, in order.
- **Effort** — `S` (a day or two), `M` (about a week), `L` (multiple weeks),
  `XL` (a month or more). This is _your time_, the scarce resource here.
- **Money cost** — almost always **$0** on the current free tiers (your machine,
  Neon free, Redis Cloud free, Mistral free tier). Exceptions are called out.
- **Risks / dependencies** — what could go wrong or must come first.

**The cost model, once, up front.** Building features costs **time, not money**.
Money only enters at _scale and public hosting_ — a 24/7 host for the API +
worker, outgrowing a free DB/Redis tier, or switching to a paid model
(Claude/GPT/Vertex). None of the ideas below trigger spend just by existing; you
can build and run all of them locally on free tiers indefinitely.

The ideas are grouped into four buckets:

1. **Breadth** — what the system can _read_ (more languages).
2. **Depth** — what it can _understand_ (history, architecture, data flow).
3. **Synthesis** — what it _produces unprompted_ (overviews, tours, diagrams).
4. **Reach** — _where_ the understanding gets used (IDE, PRs, private repos).

Plus a fifth cross-cutting bucket, **Quality & Trust**, and a sixth,
**Product surface** (things that make it feel like a real product).

---

## 1. Breadth — what it can read

### 1.1 TypeScript / JavaScript support

- **What it is.** Index and answer questions about TypeScript/JavaScript repos,
  not just Python.
- **Why it matters.** This is the single biggest ceiling-raiser. The system is
  Python-only today, which excludes most of GitHub. TS/JS is the largest slice
  of what people actually want to understand.
- **How it fits the current architecture.** Two stages care about language, and
  they differ enormously in cost:
  - **Chunking (cheap).** The chunker boundaries come from tree-sitter AST nodes
    (`SPEC §2.3`). `tree-sitter-typescript` grammar already exists; the work is
    writing queries for TS node kinds (functions, methods, classes, arrow
    functions, interfaces, type aliases). Downstream — embeddings, retrieval,
    the agent tools — is language-agnostic and does **not** change.
  - **Symbol resolution (expensive).** The symbol graph is the project's
    differentiator ("the graph reaches what retrieval cannot"). For Python you
    get it almost free via **Jedi** (`SPEC §6.1`). TypeScript has **no equally
    easy drop-in**. Proper resolution needs the TypeScript compiler tooling
    (`tsserver` / `ts-morph`), which is a _different runtime_ (Node, not Python)
    and _harder semantics_ (tsconfig path aliases, `node_modules`, barrel
    re-exports, generics, ambient declarations, JS interop).
- **Implementation sketch.**
  1. Add the TS grammar + chunking queries (`app/ingest/`), gated by file
     extension — respects `CLAUDE.md` rule 9 (new grammar ⇒ SPEC update +
     DECISIONS entry).
  2. Stand up a small **TS resolution sidecar** (Node service using `ts-morph`)
     that the ingest worker calls to produce import/call edges — mirrors the
     `§16` inference-service pattern (a language-specific helper the worker
     talks to over HTTP).
  3. Map its output into the existing `symbols` / `edges` tables — no schema
     change, since those tables are language-neutral.
  4. Re-run `scripts/eval.py` on a TS benchmark repo to prove the graph
     traversal helps on TS the way it does on Python (otherwise you shipped
     retrieval, not the thesis).
- **Effort.** **L–XL.** Chunking is `S`; resolution (steps 2–4) is where the
  weeks go.
- **Money cost.** $0 (the sidecar runs on the same box).
- **Risks / dependencies.** Resolution quality is the make-or-break. The "~80%
  resolution is enough" budget that worked for Python via Jedi is much harder to
  hit for TS. Do this deliberately, when you have real time — not as a casual
  next step. Already flagged in the `ROADMAP.md` v2 backlog for exactly this
  reason.

### 1.2 Polyglot repositories

- **What it is.** Handle one repo that mixes languages (e.g. Python backend +
  TypeScript frontend) in a single symbol graph.
- **Why it matters.** Most real projects are not single-language. Once a second
  grammar exists, supporting a mixed repo is the natural, high-value follow-on.
- **How it fits.** The chunker already routes per-file; you would route per-file
  to the right grammar and the right resolver, then write all symbols/chunks
  into the same snapshot. Cross-language edges (a TS call into a Python API over
  HTTP) are _not_ statically resolvable and should be left out — intra-language
  graphs per file, unified corpus.
- **Implementation sketch.** Extend the per-file language dispatch from 1.1;
  ensure the embedder and retrieval treat all chunks uniformly (they already
  do); tag each chunk with its language for optional filtering.
- **Effort.** **M** (assuming 1.1 is done).
- **Money cost.** $0.
- **Risks.** Scope discipline — do not attempt cross-language edge resolution;
  that is a research problem, not a feature.

### 1.3 Additional languages (Go, Rust, Java, …)

- **What it is.** Each additional tree-sitter grammar + a resolver for that
  language.
- **Why it matters.** Broadens reach further, but with diminishing returns after
  TS/JS.
- **How it fits.** Same two-stage shape as 1.1. Some languages have friendlier
  resolution stories than TS (e.g. `gopls` for Go, `rust-analyzer` for Rust),
  but each is still a new toolchain.
- **Effort.** **L** each.
- **Money cost.** $0.
- **Risks.** Only worth it once the multi-language _machinery_ from 1.1/1.2
  exists so each new language is "add a grammar + a resolver," not a rebuild.

---

## 2. Depth — what it understands

### 2.1 Git-history awareness (`search_commits`, blame) — **BUILT 2026-08-02**

- **What it is.** A new agent tool (and ingest pass) that makes commit history
  queryable: _"when was this introduced and why?"_, _"what changed here
  recently?"_, _"who last touched this?"_
- **Why it matters.** Turns "what does the code do" into "how did it evolve /
  why is it like this" — often the _real_ onboarding question. Nothing else in
  the system answers it.
- **How it fits.** The snapshots model is already **commit-pinned**
  (`repo_snapshots.commit_sha`, `SPEC §14`), so the foundation exists. You would
  index commit metadata (message, author, date, touched files/lines) during
  ingest and expose a `search_commits` tool alongside the existing six
  (`SPEC §7.1`), keeping the 8-call cap. This is explicitly in the `ROADMAP.md`
  v2 backlog ("Commit-history indexing").
- **Implementation sketch.**
  1. During clone (`SPEC §2.1`), walk `git log` and store commits + file/line
     touch ranges in a new `commits` / `commit_files` table. _(Built as written,
     with one thing the sketch missed: §2.1 cloned `--depth 1` **because**
     history was out of scope, so there was nothing to walk. The clone had to
     deepen first — the one place this feature touches something that worked.)_
  2. Optionally embed commit messages for semantic search over "why." _(Not
     built. It is the only half that needs a model, and it is a separate
     feature — see the note under step 3.)_
  3. ~~Add `search_commits(query | path | symbol)` as a 7th tool~~ — **rejected,
     and this was the significant call.** §18.1 had already settled it the other
     way: the agent's budget is 8 executions and Phase 5 reached it, so a 7th
     tool changes how the existing eight get spent. "Who last touched this" is
     a `WHERE` clause. Shipped as `GET /repos/{id}/history` instead; the tool
     count is still six and the agent loop is untouched.
  4. ~~Add a "blame" affordance in the code viewer~~ — shipped as per-file
     history rather than per-line blame: the strip answers "how did this file
     get here", which is the question a reader of a file actually has.
- **Effort.** **M.** _(Actual: M — genuinely, unlike 2.2 and 3.3. This one adds
  a migration, an ingest pass, an endpoint and a surface, and is the first item
  since the §18 batch that touches ingest at all.)_
- **Money cost.** $0, and **zero model calls** — the property that made it the
  right pick over 3.2/3.4/5.1, all of which multiply runs against the 20/day
  tier.
- **Risks.** Correctly identified: `HISTORY_MAX_COMMITS` (500) is both the clone
  depth and the row cap, deliberately one number — fetching 500 and storing 200
  pays the network cost without the benefit.
- **Shipped as.** `GET /repos/{id}/history?path=&include_merges=&limit=`
  (SPEC §20.2), plus `012_commits.sql` and a `git log --numstat` pass that
  **never fails an ingest** — history is an enrichment, and a corpus that is
  otherwise complete should not be lost to it.
- **Surfaced as.** A collapsed strip in the code viewer under the coverage one:
  subject, short sha, author, relative time, and the line deltas _for that
  file_. Merges are excluded by default (`is_merge` is stored, so it stays a
  query-time decision, per §2.6's flag-and-filter).
- **The part worth stealing.** The response carries an `indexed` flag, because
  `commits: []` means either "no commits in the window" or "nobody walked the
  log" — and the second is true of every snapshot that existed when this
  shipped. Rendering both as "no history" would state a falsehood about a repo
  with years of it. That is the §18.3 empty-not-404 reasoning one level up, and
  it is the direct lesson of `/coverage` being silently degraded by the
  src-layout bug earlier the same day.
- **Backfilled, not just built.** History needs no embedding and no chunking, so
  `scripts/backfill_history.py` fills in snapshots that predate §20 without
  touching `chunks`, `symbols`, `edges` or a vector — which is what makes it
  safe on the frozen benchmark. It walks each snapshot's **pinned commit**, not
  HEAD. All 11 ready snapshots now carry history; httpx still verifies at
  `825 | 697` with 2304 edges, unchanged.
- **A limitation claimed here and then retired.** The first version of this
  entry recorded that a commit body whose final line is exactly
  `<int>\t<int>\t<path>` was lost to the file list, "the alternative being a
  second pass over the log". There is no second pass: one ETX byte after `%b`
  terminates the body exactly. The claim was made on the strength of an
  alternative I had not looked for, and it hid a second bug beside it (a US byte
  in a body truncated it). Both are regression tests now. See DECISIONS
  2026-08-02.

### 2.2 Architecture-level understanding — **BUILT 2026-07-31**

- **What it is.** Answer _global_ questions, not just local ones: _"what are the
  main modules and how do they depend on each other?"_, _"what are the entry
  points?"_
- **Why it matters.** Today the agent excels at _local_ questions (find a
  function, trace a call). The symbol graph is your most under-used asset for
  _global_ structure — the thing a newcomer needs first.
- **How it fits.** You already build a symbol graph (`symbols` + `edges`). A
  module-dependency view is an _aggregation_ of the import edges you already
  have — group by file/package, count cross-module edges, rank by fan-in/fan-out.
  No new extraction, just a new query + a synthesis prompt.
- **Implementation sketch.**
  1. Add a query that rolls import/call edges up to module granularity.
  2. Add an `architecture_overview` capability the agent (or a batch job) can
     call to get the module map as structured data.
  3. Feed that map to the model to narrate "here are the layers and how they
     relate," with citations to the key files.
- **Effort.** **M.** _(Actual: hours, not a week — the estimate assumed new
  extraction. There is none: `symbols.file_path` is the module key and the
  rollup is two `GROUP BY`s over tables that have existed since `004`.)_
- **Money cost.** $0 — and **zero model calls**, which is the point.
- **Risks.** Ranking "important" modules well is heuristic; start with
  fan-in/fan-out and iterate.
- **Shipped as.** `GET /repos/{id}/architecture` (SPEC §18.2), _not_ an agent
  capability: the answer is exact SQL, so spending from the 8-call budget on it
  would buy nothing and cost reproducibility. Same-file edges excluded;
  `include_tests` off by default per §6.3.
- **Surfaced as.** The Architecture panel on `/repos/[id]`: modules ranked by
  fan-in with a bar relative to the top module, each expanding into _Depends
  on_ / _Used by_ and a pre-filled question via `?q=`. On httpx it ranks
  `_exceptions.py` (fan-in 80, fan-out 2 — the leaf everything imports) above
  `_models.py` (71/108 — the hub), which is the right answer and is why the
  ranking is worth showing at all.

### 2.3 Call-hierarchy & data-flow tracing — **BUILT 2026-08-02**

- **What it is.** _"Trace how a request flows end to end,"_ or _"what calls this,
  transitively, and what does it call?"_ — multi-hop graph walks surfaced as a
  path.
- **Why it matters.** This is the deepest form of "understanding" and directly
  showcases the graph thesis.
- **How it fits.** `expand_context` and `find_references` already do one hop;
  this is a bounded transitive walk (respecting the `EXPAND_MAX_DEPTH` /
  `EXPAND_TOKEN_BUDGET` constants in `SPEC §12`) rendered as an ordered path.
- **Implementation sketch.** A tool or endpoint that BFS/DFS-walks the `edges`
  table from a seed symbol to a bounded depth, dedupes, and returns an ordered
  list of `(symbol, file:line)` steps the frontend can render as a trace.
- **Effort.** **M.**
- **Money cost.** $0, zero model calls.
- **Risks.** Correctly identified, and bounded: `TRACE_MAX_DEPTH` 4,
  `TRACE_MAX_NODES` 200, plus a per-branch cycle guard without which a
  recursive CTE over a call graph does not return a big result — it does not
  return.
- **Shipped as.** `GET /repos/{id}/trace?symbol=&direction=&depth=` (SPEC §24)
  and a Trace panel on the repo page. Pointers, not bodies: the one-hop tools
  return code for a model, this returns a path for a person.
- **The correction real output forced.** Tracing `httpx._client.Client` returned
  **one** node — `BaseClient`, via `extends`. True about the class symbol and
  useless as an answer, because a class's calls live in its methods. Seeding the
  walk with the class's members turns 1 node into 78. Same failure shape as
  6.5's example-app step: technically correct, practically wrong, invisible to a
  structural test.

### 2.4 Test ↔ code linkage — **BUILT 2026-07-31**

- **What it is.** _"Which tests cover this function?"_ and the reverse.
- **Why it matters.** Cheap, high-signal onboarding aid — tests are executable
  documentation.
- **How it fits.** You already flag `is_test` at ingest (`SPEC §2.6`) and resolve
  call edges. A test-to-impl link is just: call edges _from_ test symbols _into_
  implementation symbols, which you can already compute.
- **Implementation sketch.** Add a query/tool that, given an impl symbol,
  returns test symbols with an edge into it (and vice versa). Surface as chips.
- **Effort.** **S–M.** _(Actual: S. `queries.implementation_callers` already
  had the shape; this is the same join with the `is_test` filter inverted.)_
- **Money cost.** $0.
- **Risks.** Minimal; relies on resolution quality you already have.
- **Shipped as.** `GET /repos/{id}/coverage?path=` (SPEC §18.3), both
  directions. An unknown path returns empty lists rather than 404 — a 404 would
  make it an existence oracle for paths (§13.5 reasoning, one level down).
- **Surfaced as.** A collapsed strip under the code-viewer header, hidden
  entirely when there is no linkage. Open, every test is a button that moves the
  viewer to it — jumping from a function to the test that exercises it is the
  whole point, and it reuses the same selection a citation click drives.
- **Honest limitation.** Coverage is thin on modules whose symbols are reached
  through a re-export rather than called directly: httpx's `_exceptions.py` shows
  2 linked symbols, because tests mostly say `pytest.raises(httpx.ReadTimeout)`
  and the edge resolves through `httpx/__init__.py`. Real linkage, honestly
  partial — not a bug, and worth knowing before reading the numbers as coverage.

### 2.5 Dependency / third-party understanding — **BUILT 2026-08-02**

- **What it is.** Parse `requirements.txt` / `pyproject.toml` (and later
  `package.json`) and answer _"what libraries does this use, and where?"_
- **Why it matters.** A big part of understanding a repo is understanding what
  it _stands on_. Also a stepping-stone to security/licence awareness later.
- **How it fits.** Manifest parsing is a small ingest add; "where is dep X used"
  is a search over import edges you already have (unresolved external imports are
  currently dropped — `SPEC §6.1` notes ~45% of sites are external; capturing
  _those_ is the feature).
- **Implementation sketch.** Parse manifests into a `dependencies` table; keep
  (don't drop) external import edges tagged as external; add a tool to list
  deps and their usage sites.
- **Effort.** **M.** Confirmed.
- **Money cost.** $0 — and zero model calls; the whole panel is three SQL reads.
- **Risks.** The stated risk did not materialise, because the sketch's approach
  was not taken. It said to capture the external import edges `§6.1` discards;
  doing that would have made the answer depend on **what happened to be
  installed** in the ingest environment. Imports are read from the tree-sitter
  AST instead, so `§6.1` is untouched, retrieval is untouched, and no flag or
  re-run was needed.
- **Shipped as.** `GET /repos/{id}/dependencies` and
  `GET /repos/{id}/dependencies/{module}` (SPEC §26), plus migration `015`.
  Three lists: what is imported, what is imported but undeclared, and what is
  declared but never imported. Not an agent tool — exact SQL, so the 8-call
  budget would buy nothing (the 2.1 precedent).
- **Surfaced as.** A Dependencies panel on `/repos/[id]`, packages ranked by
  import count with a bar, each expanding into its exact import sites.
- **What only real repos revealed.** Two false-positive classes that no amount
  of reasoning would have found: `_typeshed` (a type-checker-only module,
  imported 8× in flask, not installable) and five in-repo packages under
  `tests/test_apps/` that a depth-1 scan reads as undeclared dependencies. Also
  `dotenv` vs `python-dotenv` — one package reported as both undeclared _and_
  unused until an alias table reconciled them. See DECISIONS 2026-08-02.

---

## 3. Synthesis — what it produces unprompted

> This is the highest-leverage bucket for the _stated promise_ ("understand an
> unfamiliar codebase in minutes"), because it removes the "what do I even ask?"
> problem.

### 3.1 Auto-generated repo overview — **BUILT 2026-07-31** _(was the top pick)_

- **What it is.** The moment indexing finishes, synthesize a **"Start here"**
  guide: what the project does, its architecture, entry points, key modules, how
  to run it — every claim carrying `file:line` citations.
- **Why it matters.** Turns a passive Q&A box into something that _greets_ a
  newcomer with a map. It delivers the landing-page tagline better than the chat
  does, and it is the most _demoable_ single upgrade.
- **How it fits.** It reuses everything: run a fixed set of the agent's own tools
  (`list_directory`, `search_code`, `get_definition`) against a curated set of
  prompts, then synthesize with the model — the exact machinery you already
  have. Store the result on the (immutable) snapshot so it is computed **once**
  per corpus and cached forever (the same immutability that makes the V5 answer
  cache correct — `SPEC §14.3`).
- **Implementation sketch.**
  1. Add an `overview` generation step at the end of ingest (or lazily on first
     view), producing a structured doc: summary, entry points, module map
     (from 2.2), "how to run" (from README + config detection), key files.
  2. Persist it keyed on `snapshot_id` (immutable ⇒ no invalidation).
  3. Render it as the repo's landing tab on `/repos/[id]` (the page you just
     enhanced) — above the "Ask" CTA, so the overview _is_ the first thing seen.
  4. Each section links into chat with a pre-filled question (`?q=`), so the
     overview becomes launch-points for deeper questions.
- **Effort.** **M.** Best value-to-effort ratio in this document — and it was,
  though most of the effort went where the risk note below predicted.
- **Money cost.** $0, and the mechanism matters: **one model call per snapshot**,
  not one agent run. The sketch above assumed the 8-call loop; using it would
  have made a handful of repo pages a whole day of the 20-req/day tier. The
  facts are gathered by SQL (reusing 2.2's rollup) and synthesised once.
- **Risks.** Exactly right, and this was the whole cost: quality is a prompt
  problem. Three live runs to get there — the first wrote comma-separated
  citation lists (2 of 15 validated), the second invented `:1-?` placeholders
  for facts that shipped without line ranges, the third landed at 21 of 25 with
  none malformed. See DECISIONS 2026-07-31.
- **Shipped as.** `GET /repos/{id}/overview`, generated lazily on first view and
  claimed by a primary key so concurrent viewers cannot both spend a request.
  Four fixed sections rendered above the Architecture panel, each with an "ask
  more" link through `?q=`.
- **Correction to the sketch.** Step 1 lists _"how to run"_ from the README.
  There is no README — `filters.py` indexes `*.py` only. The prompt now
  explicitly forbids that section rather than letting the model recall how
  similar projects usually work.

### 3.2 Guided tours

- **What it is.** _"Walk me through how auth works"_ rendered as a **narrated,
  multi-step path** through the code (step 1 here, step 2 there…), each step
  cited, rather than one dense answer.
- **Why it matters.** Matches how a senior engineer actually onboards someone —
  a tour, not a paragraph.
- **How it fits.** A specialization of the agent loop: the model plans a sequence
  of stops (using the graph), and the SSE stream renders them as an ordered tour
  the code viewer follows. Reuses the citation-viewer sync you already built.
- **Effort.** **M–L.**
- **Money cost.** $0.
- **Risks.** Keeping tours coherent and bounded — cap the number of stops.

### 3.3 Diagram generation (mermaid) — **BUILT 2026-08-02**

- **What it is.** Auto-generate architecture / call / module diagrams from the
  symbol graph, rendered as mermaid.
- **Why it matters.** A picture of the module graph is worth a thousand answers
  for orientation.
- **How it fits.** The `edges` table _is_ a graph; emitting mermaid is a
  serialization of the module-level rollup from 2.2. Mermaid renders natively in
  the docs tooling already, so the primitive is familiar.
- **Implementation sketch.** Module rollup query → mermaid string → render in a
  diagram tab; make nodes click-through to the file. _(Accurate, with one
  correction: there is no query. 2.2's response was already on the page, so the
  first arrow in that chain does not exist and the feature adds no endpoint and
  no request.)_
- **Effort.** **M.** _(Actual: an afternoon — for the same reason 2.2 came in
  under its estimate. Nothing was computed that did not already exist.)_
- **Money cost.** $0, and **zero model calls** — same property as 2.2, inherited
  rather than re-earned.
- **Risks.** Exactly right, and it bit on the first try: twelve modules drawn
  with all 45 of their edges was a ball of string with no visible structure.
  Fixed by the top-N the risk note prescribed — `DIAGRAM_MAX_NODES` 12,
  `DIAGRAM_MAX_EDGES` 18, everything cut counted in the caption.
- **Shipped as.** A list/diagram toggle on the existing Architecture panel
  (SPEC §18.6), _not_ a separate tab: same data, same ranking, one click apart.
  `mermaid@11` is dynamically imported (~500 KB, larger than the rest of the
  page) so a reader who never opens the diagram pays nothing for it. Clicking a
  box opens that module in the list — done by reading the node id back out of
  the rendered SVG, because mermaid's `click` directive needs
  `securityLevel: "loose"` and the diagram text is built from repo paths.
- **Honest limitation.** The toggle is hidden entirely when the rollup has no
  cross-module edges, because a row of disconnected boxes says less than the
  list does. On a repo whose graph is thin, the feature correctly declines to
  appear — which also means its absence is not a bug report.

### 3.4 Docstring / README / comment generation _(new)_

- **What it is.** Generate missing docstrings, a draft README, or a
  module-summary comment — grounded in the actual code.
- **Why it matters.** Flips the tool from "read-only understanding" to "helps you
  _document_ the thing you just understood."
- **How it fits.** Same retrieve-then-synthesize loop; output is prose keyed to a
  symbol. Kept read-only (proposes text; never writes to the repo) to stay
  within scope and safety.
- **Effort.** **M.**
- **Money cost.** $0.
- **Risks.** Hallucinated docs — require citations and mark output as a _draft_.

### 3.5 "Explain this symbol / file" quick action — **BUILT 2026-07-31**

- **What it is.** A one-click "explain" on any file or symbol in the viewer, no
  typed question needed.
- **Why it matters.** Removes friction; the most common request ("what is this?")
  becomes a click.
- **How it fits.** A pre-templated question routed through the existing chat
  pipeline; pairs perfectly with the code viewer you just enhanced.
- **Effort.** **S.** Confirmed — and it should have been ranked first, not
  fourth: it is the best value-per-hour item in this document.
- **Money cost.** $0.
- **Risks.** None material.
- **Shipped as.** An "Explain" button in the code viewer that sends a
  templated question built from the open citation, plus **`?q=` prefill** on
  `/repos/[id]/chat`. The `?q=` half is the reusable part: any future surface
  (3.1's overview, 6.5's checklist) can now hand off into chat with one link.

---

## 4. Reach — where the understanding gets used

### 4.1 Private repositories

- **What it is.** Let a signed-in user index their **private** GitHub repos.
- **Why it matters.** People most want to understand _their company's_ code, not
  public OSS. High real-world relevance.
- **How it fits.** Small _architectural_ lift: you chose GitHub OAuth partly for
  this — the user's OAuth token is already the credential a private clone needs
  (`SPEC §13.1` calls this out explicitly). The work is scope, token storage, and
  security, not new pipeline.
- **Implementation sketch.**
  1. Request the `repo` scope on OAuth consent (currently `read:user` only —
     `SPEC §13.8`), ideally opt-in per user.
  2. Store the token encrypted; use it for `git clone` of private repos.
  3. Enforce that private snapshots are never shared across tenants (your V1
     ownership rule + V2 snapshots already isolate by user, but re-verify the
     dedup path does not leak a private corpus between users).
- **Effort.** **M.**
- **Money cost.** $0 (though private repos tend to be bigger — watch the free-tier
  DB storage ceiling at scale).
- **Risks.** **Security is the whole game here** — token handling, scope
  minimization, and making absolutely sure a private snapshot cannot be
  deduplicated into another user's library. Listed as v3 in `V2.md` for good
  reason; treat it as a security feature, not a convenience one.
- **Deferred 2026-08-03**, deliberately and not for effort. Scoped out first,
  and step 1 above is the part that is wrong: **"request the `repo` scope" is
  not a small change, because GitHub has no read-only private-repo scope for an
  OAuth App.** `repo` is all-or-nothing — full read _and write_ to every private
  repository the user owns, to run a tool that only ever needs to read. Asking a
  reviewer for that in order to index a codebase is a bad trade and an
  unattractive consent screen.

  The alternative is a **GitHub App** rather than an OAuth App: installation
  grants are per-repository and read-only, which is the permission this feature
  actually wants. It is not a scope change — it is a second auth integration
  (installation tokens, a webhook secret, a different callback), so §13 gains a
  path rather than a parameter.

  Two more things to settle before any of it: where the encryption key lives and
  what happens when it rotates, and a hard proof that the §14.4 commit-SHA dedup
  cannot hand a private corpus to a second user who submits the same URL. That
  last one is the leak worth being paranoid about, and it is a test to write
  before a line of clone code changes.

### 4.2 VS Code / IDE extension

- **What it is.** Ask questions about the repo you have _open in your editor_,
  without a browser.
- **Why it matters.** This is _where a tool like this actually gets adopted_ —
  understanding happens in the editor.
- **How it fits.** Your backend is already an HTTP + SSE API with a clean
  contract (`SPEC §8/§9`). An extension is a **new client** over that same API —
  no backend change. The hardest part is deciding how the extension points at an
  already-indexed snapshot (by repo URL + commit).
- **Effort.** **L** (a whole new surface + its own release story).
- **Money cost.** $0 to build; publishing to the VS Code marketplace is free.
- **Risks.** It is a separate product with its own maintenance; scope it as such.

### 4.3 GitHub PR bot / App

- **What it is.** _"Explain what this PR changes and what it affects,"_ posted as
  a PR comment.
- **Why it matters.** Meets developers in their review workflow; very shareable.
- **How it fits.** Combines git-history (2.1) + call-hierarchy (2.3): given a
  diff, find touched symbols, walk the graph for impact, synthesize. A GitHub App
  webhook triggers an agent run and posts the result.
- **Effort.** **L.**
- **Money cost.** $0 to build; a GitHub App is free. (Public 24/7 hosting for the
  webhook is the usual "scale" caveat.)
- **Risks.** Requires 2.1/2.3 to be genuinely useful; otherwise it is just chat
  in a comment.

### 4.4 Multi-turn conversation memory — **BUILT 2026-08-02**

- **What it is.** Real follow-ups — _"and where is that called?"_ — answered with
  the prior turns as context, plus saved/named conversations across sessions.
- **Why it matters.** Makes it feel like a colleague, not a stateless search box.
- **How it fits.** Today transcripts persist only in `sessionStorage`
  (`use-repo-chat.ts`) and each answer is largely self-contained. You would give
  the agent prior-turn context and (server-side) a `conversations` table so
  history survives across devices/sessions.
- **Effort.** **M.** Accurate.
- **Money cost.** $0, and the parenthetical was the whole design problem rather
  than an aside. "Slightly more tokens per turn" is true only _because_ of the
  trimming it recommends; without it six whole answers dwarf the system prompt
  and the question together.
- **Risks.** Correctly identified, and bounded twice: `CONVERSATION_CONTEXT_TURNS`
  (6, the most **recent** six — a window anchored at the start drifts away from
  the question) and `CONVERSATION_ANSWER_CHARS` (1_200, answers only; questions
  are kept whole because they are what a follow-up refers back to). Truncation
  is marked, not silent.
- **Shipped as.** SPEC §23 — `014_conversations.sql`, an optional
  `conversation_id` on `POST /chat`, and `GET`/`DELETE
/repos/{id}/conversations[/{cid}]`. The `done` event now carries the id, so
  the client captures it on turn one and sends it thereafter.
- **The decision worth stealing.** A conversation is scoped to a **snapshot**,
  not a source: its stored citations resolve against one immutable corpus, so
  replaying a thread against a newer snapshot of the same repo would point its
  own chips at lines that have moved.
- **Not built.** Generated conversation titles. The title is the first question,
  trimmed — a model call would buy something worse than what the user typed.

### 4.5 CLI enhancements & scripting — **BUILT 2026-07-31**

- **What it is.** Harden the existing `app.agent.cli` / `app.ingest.cli` into a
  batch/scriptable tool (`--json`, exit codes, pipe-friendly output).
- **Why it matters.** Lets the understanding feed _other_ tools (CI checks,
  scripts) — cheap reach with no new surface.
- **How it fits.** The CLIs already exist (`SPEC` references them); this is
  polish and output contracts.
- **Effort.** **S.**
- **Money cost.** $0.
- **Risks.** None material.
- **Shipped as.** `--json` on the ingest CLI and a JSON _error_ envelope on the
  agent CLI. The real work was not the flag: the pipeline's progress lines were
  printed to stdout, so they landed inside the document. `run_ingest`'s `log` is
  now a parameter and `--json` routes every human line to stderr — stdout is one
  object carrying `ok`, on success and on failure, and the exit code mirrors it.

### 4.6 Chat bot (Slack / Discord) _(new)_

- **What it is.** Ask about an indexed repo from a team chat channel.
- **Why it matters.** Brings the tool to where teams already talk.
- **How it fits.** Another thin client over the SSE API, like the IDE extension.
- **Effort.** **M.**
- **Money cost.** $0 to build.
- **Risks.** Same 24/7-hosting caveat as any always-on integration.

---

## 5. Quality & trust (cross-cutting)

### 5.1 Answer verification / citation grounding — **BUILT 2026-08-03**

- **What it is.** After the agent answers, verify each cited range actually
  supports the claim before showing it.
- **Why it matters.** "An answer without citations is a bug" is already the
  project's rule (`CLAUDE.md` rule 5); this goes further — citations that are
  _present but wrong_ are the subtler failure.
- **How it fits.** A post-answer pass that re-reads each cited range and checks
  relevance (cheap model call or heuristic), flagging weak citations.
- **Effort.** **M.**
- **Money cost.** $0 (one extra small call per answer).
- **Risks.** Adds latency — do it async / mark rather than block.
- **Shipped as.** SPEC §27: a lexical grounding check on the `citations` SSE
  event, three-valued (`supported` / `unsupported` / `unchecked`), plus an
  advisory block in the answer UI that lists only the unsupported ones.
- **The risk was avoided rather than managed.** "Adds latency" assumed the
  model-call design; the heuristic costs one read and no provider call, so
  there is nothing to make async. Determinism, not cost, was the deciding
  argument: a grounding badge that changes between runs cannot be learned or
  trusted.
- **`unchecked` is the load-bearing verdict.** A claim naming no identifiers
  cannot be checked, and reporting it as unsupported would invent findings out
  of the method's blind spot — which is how an advisory signal gets switched
  off, taking the real warnings with it.
- **What only a real answer revealed.** A live blinker answer scored
  `unchecked` on a claim naming `ANY` — blinker's actual sentinel — because
  "any" was in the stopword list. Stopwords no longer apply inside backticks.
  Same answer now scores 5/5 supported. See DECISIONS 2026-08-03.

### 5.2 Code-specific reranker — **MEASURED 2026-08-03, not adopted**

- **What it is.** Replace/augment the ablated general-purpose reranker with a
  code-aware one.
- **Why it matters.** The current `bge-reranker-v2-m3` measured _worse-or-equal_
  to plain fusion (`SPEC §5.3`, `DECISIONS 2026-07-26`) and is off by default; a
  code-tuned cross-encoder might actually earn its place.
- **How it fits.** The ablation is _still wired_ — `eval.py --mode hybrid+rerank`
  — so this is a swap-and-measure, not new plumbing. Explicitly in the
  `ROADMAP.md` backlog.
- **Effort.** **M** (mostly evaluation).
- **Money cost.** $0.
- **Risks.** May still not beat fusion — the win is not guaranteed, which is why
  it is measure-first.
- **Measured.** Exactly as the risk note predicted, and the result decomposed
  into something more useful than "no". A second, _general-purpose_ reranker
  27× smaller (`cross-encoder/ms-marco-MiniLM-L-6-v2`, ~90 MB vs 2.4 GB) beats
  the shipped one on hit@5, hit@10 and MRR — **and still loses to plain
  fusion**. Two unrelated models losing the same way moves the conclusion from
  "this reranker does not help" to "reranking the fused list does not help".
  hit@3 is 0.80 in all three conditions: the metric a reranker exists to move
  does not move.
- **Still open, and blocked by a stale model rather than by nerve.** The one
  credible _code-trained_ cross-encoder
  (`jinaai/jina-reranker-v2-base-multilingual`) was attempted properly:
  `RERANKER_TRUST_REMOTE_CODE` added (default off), `einops` installed. It then
  failed on transformers 5.14 removing a symbol its Hub code imports — the model
  targets 4.x. Downgrading the whole inference stack a major version, under a
  benchmark corpus embedded on the current one, costs more than the answer is
  worth against two models that already lost. `einops` removed again. Untested,
  not disproven. Numbers in SPEC §5.3 and DECISIONS 2026-08-03.

### 5.3 Incremental re-indexing on new commits

- **What it is.** Keep a repo's index fresh as it changes, instead of a full
  re-ingest.
- **Why it matters.** Real repos move; a stale index degrades quietly.
- **How it fits.** Snapshots are per-commit and immutable, so "fresh" means "a
  new snapshot at the new SHA." Incremental means _diffing_ commits and only
  re-embedding changed files rather than the whole repo. A webhook can trigger
  it. In both the `ROADMAP.md` and `V2.md` backlogs.
- **Effort.** **L.**
- **Money cost.** $0 (actually _saves_ compute vs. full re-ingest).
- **Risks.** Correctness of the diff (partial graphs) — the immutable-snapshot
  invariant helps, but graph edges spanning changed/unchanged files need care.

### 5.4 Confidence & uncertainty signals — **BUILT 2026-08-02**

- **What it is.** Let the agent say _"I'm not certain"_ or _"the code doesn't
  clearly show this,"_ rather than always answering confidently.
- **Why it matters.** Trust. A tool that hedges when the evidence is thin is more
  trustworthy than one that always sounds sure.
- **How it fits.** A prompt/system-message change plus a UI affordance; optionally
  keyed to citation strength from 5.1.
- **Effort.** **S–M.** Accurate.
- **Money cost.** $0, zero extra model calls — it rides the answer that was
  already being generated, unlike 5.1 which adds a second pass.
- **Risks.** "Over-hedging — calibrate" was the entire feature, not a caveat on
  it. The prompt names the three cases that warrant a marker, forbids the rest
  _with a reason_ (a marker on every answer is worth what a marker on none is),
  and shows the specific wrong example — the generic "I am an AI" disclaimer.
- **Shipped as.** A parsed `[uncertain: …]` marker (SPEC §25) rendered as a
  dashed callout in chat, on the §21 permalink page, and as a blockquote in the
  6.2 Markdown export. Structured rather than prose precisely so it can be
  rendered, and so over-hedging would be _countable_ rather than a feeling.
- **One thing worth knowing.** The parser is anchored to the end of the answer
  because this tool gets pointed at its own repository, and an answer explaining
  §25 would otherwise have its prose eaten by its own example.

---

## 6. Product surface (makes it feel like a real product) _(new)_

- **6.1 Shareable answer permalinks.** **BUILT 2026-08-02.** `POST
/repos/{id}/share` → `GET /shared/{id}` → `/a/{id}`, plus a publisher-only
  `DELETE`. The "safe _because_ snapshots are immutable" note was the right
  instinct and understated the work: immutability makes the _link_ honest, but
  the read is **the only route in the API with no session**, so the real cost
  was the boundary — bounded inputs, citations re-validated against the snapshot
  rather than trusted from the client, the publisher never named in the
  response, and one 404 covering never-existed / not-yours / retracted. `S–M`
  was accurate for the code and wrong for the review. SPEC §21. **4.1 must gate
  the public read before a private corpus can exist.**
- **6.2 Export a conversation to Markdown.** **BUILT 2026-07-31.** One click;
  citations become GitHub blob links at the pinned commit, so the note still
  resolves for someone without this app open. **S.**
- **6.3 Snapshot comparison.** **BUILT 2026-08-03.** `GET /repos/{id}/compare?base=`
  (SPEC §28): files, symbols and third-party packages added/removed between two
  snapshots, plus the commits between them. Structural, not textual — `git diff`
  does lines better; nothing else says what the _index_ now holds. Symbols keyed
  on qualname, not (file, line), or every symbol below an edit reads as replaced.
  **"Natural once multiple snapshots exist" was the wrong half of the estimate:**
  every source had exactly one commit, because `clone_repo` always took the
  branch tip. The real prerequisite was ingest-at-a-commit (`--rev`), which did
  not exist. The diff is four SQL statements; noticing there was nothing to diff
  was the work. **Surfaced 2026-08-04** as the Compare panel on `/repos/[id]`: a commit
  field that posts `rev` (the only place in the UI that can create a second
  snapshot), a picker of sibling commits from `GET /repos/{id}/snapshots`, and
  the diff. The gap was never the diff — it was that nothing could make a pair.
  **M–L.**
- **6.4 Cross-repo / org-wide search.** Ask across _all_ your indexed repos at
  once. Builds on V2 snapshots + multi-tenant. **L.**
- **6.5 Onboarding checklist.** **BUILT 2026-08-02.** `GET
/repos/{id}/checklist` (SPEC §22) + a panel under the overview, each step a
  `?q=` launch-point. "Pairs with 3.1" was right about placement and wrong about
  mechanism: 3.1 is one model call, this is **none** — four of the five steps
  were already `GROUP BY`s §19 ran to build its prompt. `S–M` held for the code;
  the time went into two defects only real output revealed (a step pointing at
  flask's _example app_ as the public API, and two steps citing the same range).
  A test asserts the job queue stays empty, so this cannot silently become a
  model call later.
- **6.6 Dark-mode toggle.** **BUILT 2026-07-31.** Three-state (system / light /
  dark), hand-rolled rather than `next-themes` (rule 11), with a pre-paint
  inline script so dark-mode users do not get a white flash on every navigation.
  The code viewer re-tokenises through Shiki's `vitesse-dark`, which was already
  in the bundle and had never been used. **S.**

---

## Prioritization matrix

Value is impact on the core promise; effort is your time. Money is `$0` unless
noted.

| Idea                          | Value | Effort | Reuses what exists?                | Notes                                                 |
| ----------------------------- | ----- | ------ | ---------------------------------- | ----------------------------------------------------- |
| 3.1 Auto-overview             | ★★★★★ | M      | Almost entirely                    | **BUILT** — one model call, not a loop                |
| 2.1 Git-history tool          | ★★★★  | M      | Snapshots are commit-pinned        | **BUILT** — as an endpoint; the 7th tool was rejected |
| 4.1 Private repos             | ★★★★  | M      | OAuth token already the credential | Security-first                                        |
| 3.5 Explain-this quick action | ★★★   | S      | Chat pipeline + viewer             | **BUILT** — and `?q=` fed 3.1                         |
| 2.4 Test↔code linkage         | ★★★   | S–M    | `is_test` + edges                  | **BUILT** — cheap, high signal                        |
| 2.2 Architecture overview     | ★★★★  | M      | Symbol graph rollup                | **BUILT** — and it did feed 3.1                       |
| 4.4 Multi-turn memory         | ★★★   | M      | SSE + agent                        | **BUILT** — bounded by turns _and_ answer length      |
| 3.3 Diagrams (mermaid)        | ★★★   | M      | `edges` table                      | **BUILT** — a second view of 2.2, not a second query  |
| 6.1 Answer permalinks         | ★★★   | S–M    | Immutable snapshots                | **BUILT** — the API's only unauthenticated read       |
| 6.5 Onboarding checklist      | ★★★   | S–M    | §19's own fact queries             | **BUILT** — zero model calls, unlike 3.1              |
| 2.3 Call-hierarchy trace      | ★★★   | M      | `edges` + a recursive CTE          | **BUILT** — a path, not a code dump                   |
| 5.4 Confidence signals        | ★★★   | S–M    | The answer already being written   | **BUILT** — calibration _is_ the feature              |
| 5.3 Incremental re-index      | ★★★   | L      | Snapshots                          | Freshness; saves compute                              |
| 4.2 IDE extension             | ★★★★★ | L      | The whole API                      | Adoption, but a new surface                           |
| 1.1 TypeScript                | ★★★★★ | L–XL   | Chunking only; resolution is new   | Highest ceiling, biggest investment                   |
| 4.3 PR bot                    | ★★★   | L      | Needs 2.1/2.3 first                | Shareable                                             |

---

## Recommended build sequence

A path that front-loads value and defers the big investments, each step
standing on the last:

1. **3.1 Auto-generated overview** — biggest promise-delivery per hour; makes the
   repo page (which you just enhanced) _the_ onboarding surface.
2. ~~**2.2 Architecture rollup** + **3.3 diagrams**~~ — **done.** Both fell out of
   the graph, as predicted; 3.3 turned out to need no query at all.
3. ~~**2.1 Git-history tool**~~ — **done.** The endpoint/tool split above was the
   right call and is now the built shape; the semantic-search-over-messages half
   remains unbuilt and is the only part that would need a model.
4. ~~**3.5 explain-this** + **2.4 test↔code** + **6.x product polish**~~ —
   **done.** 3.5, 2.4, 6.1, 6.2, 6.5 and 6.6 all shipped.
5. ~~**4.4 multi-turn memory**~~ — **done.** SPEC §23.
6. **4.1 private repos** — unlock real-world usage (do the security work
   carefully).
7. **1.1 TypeScript** — the big investment, taken deliberately once the rest is
   solid and you have time to commit.
8. **4.2 IDE extension / 4.3 PR bot** — new surfaces, once the core is rich enough
   to be worth embedding elsewhere.

---

## Relationship to the existing plans

- **`ROADMAP.md`** (v1, phases 0–6) is done bar a demo GIF; its backlog already
  names TypeScript (1.1), commit-history (2.1), private repos (4.1), the
  code-specific reranker (5.2), and incremental re-index (5.3). This document
  elaborates and prioritizes those, and adds the synthesis/reach/quality ideas.
- **`V2.md`** (multi-tenant, V1–V5): V1–V3 done, V4–V5 pending. Several ideas here
  (private repos, cross-repo search, answer permalinks) build directly on the V2
  snapshot + tenancy model.
- Anything that adds an **agent tool** (2.1, 2.3, 2.4) must respect the 8-call cap
  and needs a `SPEC §7.1` update + a `DECISIONS.md` entry. Anything that adds a
  **grammar** (1.1, 1.3) needs the same per `CLAUDE.md` rule 9. Keep this catalogue
  as ideas; promote an item into `ROADMAP`/`V2` with real done-when criteria when
  you decide to build it.

---

_This is a living idea list. Add, cut, and re-rank freely — the point is a clear
menu, not a contract._
