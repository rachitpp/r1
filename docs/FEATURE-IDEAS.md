# FEATURE-IDEAS.md — extending the core

> **What this is.** A catalogue of forward-looking ideas for growing what this
> project *does* — turning "an unfamiliar repo into cited answers" into
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
> 6.2 and 6.6 — and are marked as such below, each with what shipped *and* what it looks
> like to a user. (2.2 and 2.4 landed as endpoints first and were briefly "built"
> with no consumer; both now have a surface.) They were taken together because none of
> them touches ingest or retrieval, so none could disturb the eval-equality
> verification V2/V3 rest on. See SPEC §18 and DECISIONS 2026-07-31.
>
> **Status, 2026-08-02 (later).** **2.1** built as SPEC §20, bringing the total
> to **nine**. Notable as the first item since the §18 batch to touch ingest: it
> needed a migration, a `git log` pass, and a change to §2.1's depth-1 clone,
> which existed *because* history was out of scope. The catalogue's sketch called
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
> 26 unchanged). httpx is exempt *by construction* — a flat layout yields no
> import root — and its `825 | 697` invariant verifies. Only three throwaway
> `rachitpp/*` submissions from 07-28 still hold pre-fix graphs, and nothing
> reads them. So `architecture`, `coverage`, `overview` and the diagram are all
> trustworthy on every corpus that matters.
>
> **The cost model below understates one thing, and it is the important one.**
> "$0 unless noted" is true about *invoices* and misleading about *capacity*.
> The real currency is provider rate limits: `app/agent/model.py` records that
> the AI Studio key's actual ceiling is **20 requests/day/model — two agent
> runs**, which is what forced the documented Mistral/Gemini/Vertex role split.
> So 3.1 (one cached run per snapshot) is genuinely free, while 3.2, 3.4, 4.3
> and 5.1 all *multiply* runs against a tier that has already proven too thin
> once. Weigh those against quota, not against dollars.
>
> **Two corrections to the text below.** (1) "V1–V3 done" in *Relationship to
> the existing plans* is optimistic: V2.md shows V1 at `[~]` (the auth'd chat
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
  `XL` (a month or more). This is *your time*, the scarce resource here.
- **Money cost** — almost always **$0** on the current free tiers (your machine,
  Neon free, Redis Cloud free, Mistral free tier). Exceptions are called out.
- **Risks / dependencies** — what could go wrong or must come first.

**The cost model, once, up front.** Building features costs **time, not money**.
Money only enters at *scale and public hosting* — a 24/7 host for the API +
worker, outgrowing a free DB/Redis tier, or switching to a paid model
(Claude/GPT/Vertex). None of the ideas below trigger spend just by existing; you
can build and run all of them locally on free tiers indefinitely.

The ideas are grouped into four buckets:

1. **Breadth** — what the system can *read* (more languages).
2. **Depth** — what it can *understand* (history, architecture, data flow).
3. **Synthesis** — what it *produces unprompted* (overviews, tours, diagrams).
4. **Reach** — *where* the understanding gets used (IDE, PRs, private repos).

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
    (`tsserver` / `ts-morph`), which is a *different runtime* (Node, not Python)
    and *harder semantics* (tsconfig path aliases, `node_modules`, barrel
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
  HTTP) are *not* statically resolvable and should be left out — intra-language
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
- **Risks.** Only worth it once the multi-language *machinery* from 1.1/1.2
  exists so each new language is "add a grammar + a resolver," not a rebuild.

---

## 2. Depth — what it understands

### 2.1 Git-history awareness (`search_commits`, blame) — **BUILT 2026-08-02**

- **What it is.** A new agent tool (and ingest pass) that makes commit history
  queryable: *"when was this introduced and why?"*, *"what changed here
  recently?"*, *"who last touched this?"*
- **Why it matters.** Turns "what does the code do" into "how did it evolve /
  why is it like this" — often the *real* onboarding question. Nothing else in
  the system answers it.
- **How it fits.** The snapshots model is already **commit-pinned**
  (`repo_snapshots.commit_sha`, `SPEC §14`), so the foundation exists. You would
  index commit metadata (message, author, date, touched files/lines) during
  ingest and expose a `search_commits` tool alongside the existing six
  (`SPEC §7.1`), keeping the 8-call cap. This is explicitly in the `ROADMAP.md`
  v2 backlog ("Commit-history indexing").
- **Implementation sketch.**
  1. During clone (`SPEC §2.1`), walk `git log` and store commits + file/line
     touch ranges in a new `commits` / `commit_files` table. *(Built as written,
     with one thing the sketch missed: §2.1 cloned `--depth 1` **because**
     history was out of scope, so there was nothing to walk. The clone had to
     deepen first — the one place this feature touches something that worked.)*
  2. Optionally embed commit messages for semantic search over "why." *(Not
     built. It is the only half that needs a model, and it is a separate
     feature — see the note under step 3.)*
  3. ~~Add `search_commits(query | path | symbol)` as a 7th tool~~ — **rejected,
     and this was the significant call.** §18.1 had already settled it the other
     way: the agent's budget is 8 executions and Phase 5 reached it, so a 7th
     tool changes how the existing eight get spent. "Who last touched this" is
     a `WHERE` clause. Shipped as `GET /repos/{id}/history` instead; the tool
     count is still six and the agent loop is untouched.
  4. ~~Add a "blame" affordance in the code viewer~~ — shipped as per-file
     history rather than per-line blame: the strip answers "how did this file
     get here", which is the question a reader of a file actually has.
- **Effort.** **M.** *(Actual: M — genuinely, unlike 2.2 and 3.3. This one adds
  a migration, an ingest pass, an endpoint and a surface, and is the first item
  since the §18 batch that touches ingest at all.)*
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
  subject, short sha, author, relative time, and the line deltas *for that
  file*. Merges are excluded by default (`is_merge` is stored, so it stays a
  query-time decision, per §2.6's flag-and-filter).
- **The part worth stealing.** The response carries an `indexed` flag, because
  `commits: []` means either "no commits in the window" or "nobody walked the
  log" — and the second is true of every snapshot that existed when this
  shipped. Rendering both as "no history" would state a falsehood about a repo
  with years of it. That is the §18.3 empty-not-404 reasoning one level up, and
  it is the direct lesson of `/coverage` being silently degraded by the
  src-layout bug earlier the same day.
- **Honest limitation.** The body parser can lose a line: a commit message whose
  *final* line is exactly `<int>\t<int>\t<path>` is indistinguishable from the
  numstat block it precedes. Recorded rather than fixed — the alternative is a
  second pass over the log for a case that does not occur.

### 2.2 Architecture-level understanding — **BUILT 2026-07-31**

- **What it is.** Answer *global* questions, not just local ones: *"what are the
  main modules and how do they depend on each other?"*, *"what are the entry
  points?"*
- **Why it matters.** Today the agent excels at *local* questions (find a
  function, trace a call). The symbol graph is your most under-used asset for
  *global* structure — the thing a newcomer needs first.
- **How it fits.** You already build a symbol graph (`symbols` + `edges`). A
  module-dependency view is an *aggregation* of the import edges you already
  have — group by file/package, count cross-module edges, rank by fan-in/fan-out.
  No new extraction, just a new query + a synthesis prompt.
- **Implementation sketch.**
  1. Add a query that rolls import/call edges up to module granularity.
  2. Add an `architecture_overview` capability the agent (or a batch job) can
     call to get the module map as structured data.
  3. Feed that map to the model to narrate "here are the layers and how they
     relate," with citations to the key files.
- **Effort.** **M.** *(Actual: hours, not a week — the estimate assumed new
  extraction. There is none: `symbols.file_path` is the module key and the
  rollup is two `GROUP BY`s over tables that have existed since `004`.)*
- **Money cost.** $0 — and **zero model calls**, which is the point.
- **Risks.** Ranking "important" modules well is heuristic; start with
  fan-in/fan-out and iterate.
- **Shipped as.** `GET /repos/{id}/architecture` (SPEC §18.2), *not* an agent
  capability: the answer is exact SQL, so spending from the 8-call budget on it
  would buy nothing and cost reproducibility. Same-file edges excluded;
  `include_tests` off by default per §6.3.
- **Surfaced as.** The Architecture panel on `/repos/[id]`: modules ranked by
  fan-in with a bar relative to the top module, each expanding into *Depends
  on* / *Used by* and a pre-filled question via `?q=`. On httpx it ranks
  `_exceptions.py` (fan-in 80, fan-out 2 — the leaf everything imports) above
  `_models.py` (71/108 — the hub), which is the right answer and is why the
  ranking is worth showing at all.

### 2.3 Call-hierarchy & data-flow tracing

- **What it is.** *"Trace how a request flows end to end,"* or *"what calls this,
  transitively, and what does it call?"* — multi-hop graph walks surfaced as a
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
- **Money cost.** $0.
- **Risks.** Explosion on hot symbols — hard depth/breadth caps are essential.

### 2.4 Test ↔ code linkage — **BUILT 2026-07-31**

- **What it is.** *"Which tests cover this function?"* and the reverse.
- **Why it matters.** Cheap, high-signal onboarding aid — tests are executable
  documentation.
- **How it fits.** You already flag `is_test` at ingest (`SPEC §2.6`) and resolve
  call edges. A test-to-impl link is just: call edges *from* test symbols *into*
  implementation symbols, which you can already compute.
- **Implementation sketch.** Add a query/tool that, given an impl symbol,
  returns test symbols with an edge into it (and vice versa). Surface as chips.
- **Effort.** **S–M.** *(Actual: S. `queries.implementation_callers` already
  had the shape; this is the same join with the `is_test` filter inverted.)*
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

### 2.5 Dependency / third-party understanding *(new)*

- **What it is.** Parse `requirements.txt` / `pyproject.toml` (and later
  `package.json`) and answer *"what libraries does this use, and where?"*
- **Why it matters.** A big part of understanding a repo is understanding what
  it *stands on*. Also a stepping-stone to security/licence awareness later.
- **How it fits.** Manifest parsing is a small ingest add; "where is dep X used"
  is a search over import edges you already have (unresolved external imports are
  currently dropped — `SPEC §6.1` notes ~45% of sites are external; capturing
  *those* is the feature).
- **Implementation sketch.** Parse manifests into a `dependencies` table; keep
  (don't drop) external import edges tagged as external; add a tool to list
  deps and their usage sites.
- **Effort.** **M.**
- **Money cost.** $0.
- **Risks.** External-import capture changes what `§6.1` currently discards —
  do it behind a flag and re-run eval to confirm retrieval is unaffected.

---

## 3. Synthesis — what it produces unprompted

> This is the highest-leverage bucket for the *stated promise* ("understand an
> unfamiliar codebase in minutes"), because it removes the "what do I even ask?"
> problem.

### 3.1 Auto-generated repo overview — **BUILT 2026-07-31** *(was the top pick)*

- **What it is.** The moment indexing finishes, synthesize a **"Start here"**
  guide: what the project does, its architecture, entry points, key modules, how
  to run it — every claim carrying `file:line` citations.
- **Why it matters.** Turns a passive Q&A box into something that *greets* a
  newcomer with a map. It delivers the landing-page tagline better than the chat
  does, and it is the most *demoable* single upgrade.
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
     enhanced) — above the "Ask" CTA, so the overview *is* the first thing seen.
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
- **Correction to the sketch.** Step 1 lists *"how to run"* from the README.
  There is no README — `filters.py` indexes `*.py` only. The prompt now
  explicitly forbids that section rather than letting the model recall how
  similar projects usually work.

### 3.2 Guided tours

- **What it is.** *"Walk me through how auth works"* rendered as a **narrated,
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
- **How it fits.** The `edges` table *is* a graph; emitting mermaid is a
  serialization of the module-level rollup from 2.2. Mermaid renders natively in
  the docs tooling already, so the primitive is familiar.
- **Implementation sketch.** Module rollup query → mermaid string → render in a
  diagram tab; make nodes click-through to the file. *(Accurate, with one
  correction: there is no query. 2.2's response was already on the page, so the
  first arrow in that chain does not exist and the feature adds no endpoint and
  no request.)*
- **Effort.** **M.** *(Actual: an afternoon — for the same reason 2.2 came in
  under its estimate. Nothing was computed that did not already exist.)*
- **Money cost.** $0, and **zero model calls** — same property as 2.2, inherited
  rather than re-earned.
- **Risks.** Exactly right, and it bit on the first try: twelve modules drawn
  with all 45 of their edges was a ball of string with no visible structure.
  Fixed by the top-N the risk note prescribed — `DIAGRAM_MAX_NODES` 12,
  `DIAGRAM_MAX_EDGES` 18, everything cut counted in the caption.
- **Shipped as.** A list/diagram toggle on the existing Architecture panel
  (SPEC §18.6), *not* a separate tab: same data, same ranking, one click apart.
  `mermaid@11` is dynamically imported (~500 KB, larger than the rest of the
  page) so a reader who never opens the diagram pays nothing for it. Clicking a
  box opens that module in the list — done by reading the node id back out of
  the rendered SVG, because mermaid's `click` directive needs
  `securityLevel: "loose"` and the diagram text is built from repo paths.
- **Honest limitation.** The toggle is hidden entirely when the rollup has no
  cross-module edges, because a row of disconnected boxes says less than the
  list does. On a repo whose graph is thin, the feature correctly declines to
  appear — which also means its absence is not a bug report.

### 3.4 Docstring / README / comment generation *(new)*

- **What it is.** Generate missing docstrings, a draft README, or a
  module-summary comment — grounded in the actual code.
- **Why it matters.** Flips the tool from "read-only understanding" to "helps you
  *document* the thing you just understood."
- **How it fits.** Same retrieve-then-synthesize loop; output is prose keyed to a
  symbol. Kept read-only (proposes text; never writes to the repo) to stay
  within scope and safety.
- **Effort.** **M.**
- **Money cost.** $0.
- **Risks.** Hallucinated docs — require citations and mark output as a *draft*.

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
- **Why it matters.** People most want to understand *their company's* code, not
  public OSS. High real-world relevance.
- **How it fits.** Small *architectural* lift: you chose GitHub OAuth partly for
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

### 4.2 VS Code / IDE extension

- **What it is.** Ask questions about the repo you have *open in your editor*,
  without a browser.
- **Why it matters.** This is *where a tool like this actually gets adopted* —
  understanding happens in the editor.
- **How it fits.** Your backend is already an HTTP + SSE API with a clean
  contract (`SPEC §8/§9`). An extension is a **new client** over that same API —
  no backend change. The hardest part is deciding how the extension points at an
  already-indexed snapshot (by repo URL + commit).
- **Effort.** **L** (a whole new surface + its own release story).
- **Money cost.** $0 to build; publishing to the VS Code marketplace is free.
- **Risks.** It is a separate product with its own maintenance; scope it as such.

### 4.3 GitHub PR bot / App

- **What it is.** *"Explain what this PR changes and what it affects,"* posted as
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

### 4.4 Multi-turn conversation memory

- **What it is.** Real follow-ups — *"and where is that called?"* — answered with
  the prior turns as context, plus saved/named conversations across sessions.
- **Why it matters.** Makes it feel like a colleague, not a stateless search box.
- **How it fits.** Today transcripts persist only in `sessionStorage`
  (`use-repo-chat.ts`) and each answer is largely self-contained. You would give
  the agent prior-turn context and (server-side) a `conversations` table so
  history survives across devices/sessions.
- **Effort.** **M.**
- **Money cost.** $0 (slightly more tokens per turn as context grows — trim old
  turns; still free-tier-friendly).
- **Risks.** Context bloat inflating token use and latency — cap history window.

### 4.5 CLI enhancements & scripting — **BUILT 2026-07-31**

- **What it is.** Harden the existing `app.agent.cli` / `app.ingest.cli` into a
  batch/scriptable tool (`--json`, exit codes, pipe-friendly output).
- **Why it matters.** Lets the understanding feed *other* tools (CI checks,
  scripts) — cheap reach with no new surface.
- **How it fits.** The CLIs already exist (`SPEC` references them); this is
  polish and output contracts.
- **Effort.** **S.**
- **Money cost.** $0.
- **Risks.** None material.
- **Shipped as.** `--json` on the ingest CLI and a JSON *error* envelope on the
  agent CLI. The real work was not the flag: the pipeline's progress lines were
  printed to stdout, so they landed inside the document. `run_ingest`'s `log` is
  now a parameter and `--json` routes every human line to stderr — stdout is one
  object carrying `ok`, on success and on failure, and the exit code mirrors it.

### 4.6 Chat bot (Slack / Discord) *(new)*

- **What it is.** Ask about an indexed repo from a team chat channel.
- **Why it matters.** Brings the tool to where teams already talk.
- **How it fits.** Another thin client over the SSE API, like the IDE extension.
- **Effort.** **M.**
- **Money cost.** $0 to build.
- **Risks.** Same 24/7-hosting caveat as any always-on integration.

---

## 5. Quality & trust (cross-cutting)

### 5.1 Answer verification / citation grounding *(new)*

- **What it is.** After the agent answers, verify each cited range actually
  supports the claim before showing it.
- **Why it matters.** "An answer without citations is a bug" is already the
  project's rule (`CLAUDE.md` rule 5); this goes further — citations that are
  *present but wrong* are the subtler failure.
- **How it fits.** A post-answer pass that re-reads each cited range and checks
  relevance (cheap model call or heuristic), flagging weak citations.
- **Effort.** **M.**
- **Money cost.** $0 (one extra small call per answer).
- **Risks.** Adds latency — do it async / mark rather than block.

### 5.2 Code-specific reranker

- **What it is.** Replace/augment the ablated general-purpose reranker with a
  code-aware one.
- **Why it matters.** The current `bge-reranker-v2-m3` measured *worse-or-equal*
  to plain fusion (`SPEC §5.3`, `DECISIONS 2026-07-26`) and is off by default; a
  code-tuned cross-encoder might actually earn its place.
- **How it fits.** The ablation is *still wired* — `eval.py --mode hybrid+rerank`
  — so this is a swap-and-measure, not new plumbing. Explicitly in the
  `ROADMAP.md` backlog.
- **Effort.** **M** (mostly evaluation).
- **Money cost.** $0.
- **Risks.** May still not beat fusion — the win is not guaranteed, which is why
  it is measure-first.

### 5.3 Incremental re-indexing on new commits

- **What it is.** Keep a repo's index fresh as it changes, instead of a full
  re-ingest.
- **Why it matters.** Real repos move; a stale index degrades quietly.
- **How it fits.** Snapshots are per-commit and immutable, so "fresh" means "a
  new snapshot at the new SHA." Incremental means *diffing* commits and only
  re-embedding changed files rather than the whole repo. A webhook can trigger
  it. In both the `ROADMAP.md` and `V2.md` backlogs.
- **Effort.** **L.**
- **Money cost.** $0 (actually *saves* compute vs. full re-ingest).
- **Risks.** Correctness of the diff (partial graphs) — the immutable-snapshot
  invariant helps, but graph edges spanning changed/unchanged files need care.

### 5.4 Confidence & uncertainty signals *(new)*

- **What it is.** Let the agent say *"I'm not certain"* or *"the code doesn't
  clearly show this,"* rather than always answering confidently.
- **Why it matters.** Trust. A tool that hedges when the evidence is thin is more
  trustworthy than one that always sounds sure.
- **How it fits.** A prompt/system-message change plus a UI affordance; optionally
  keyed to citation strength from 5.1.
- **Effort.** **S–M.**
- **Money cost.** $0.
- **Risks.** Over-hedging — calibrate.

---

## 6. Product surface (makes it feel like a real product) *(new)*

- **6.1 Shareable answer permalinks.** A stable URL for a specific answer +
  citations against a snapshot (safe *because* snapshots are immutable). **S–M.**
- **6.2 Export a conversation to Markdown.** **BUILT 2026-07-31.** One click;
  citations become GitHub blob links at the pinned commit, so the note still
  resolves for someone without this app open. **S.**
- **6.3 Snapshot comparison.** *"What changed between this repo at commit A and
  commit B?"* — natural once history (2.1) and multiple snapshots exist. **M–L.**
- **6.4 Cross-repo / org-wide search.** Ask across *all* your indexed repos at
  once. Builds on V2 snapshots + multi-tenant. **L.**
- **6.5 Onboarding checklist.** Auto-generate "the first 5 things to understand
  about this repo," each a launch-point into chat. Pairs with 3.1. **S–M.**
- **6.6 Dark-mode toggle.** **BUILT 2026-07-31.** Three-state (system / light /
  dark), hand-rolled rather than `next-themes` (rule 11), with a pre-paint
  inline script so dark-mode users do not get a white flash on every navigation.
  The code viewer re-tokenises through Shiki's `vitesse-dark`, which was already
  in the bundle and had never been used. **S.**

---

## Prioritization matrix

Value is impact on the core promise; effort is your time. Money is `$0` unless
noted.

| Idea | Value | Effort | Reuses what exists? | Notes |
|---|---|---|---|---|
| 3.1 Auto-overview | ★★★★★ | M | Almost entirely | **BUILT** — one model call, not a loop |
| 2.1 Git-history tool | ★★★★ | M | Snapshots are commit-pinned | **BUILT** — as an endpoint; the 7th tool was rejected |
| 4.1 Private repos | ★★★★ | M | OAuth token already the credential | Security-first |
| 3.5 Explain-this quick action | ★★★ | S | Chat pipeline + viewer | **BUILT** — and `?q=` fed 3.1 |
| 2.4 Test↔code linkage | ★★★ | S–M | `is_test` + edges | **BUILT** — cheap, high signal |
| 2.2 Architecture overview | ★★★★ | M | Symbol graph rollup | **BUILT** — and it did feed 3.1 |
| 4.4 Multi-turn memory | ★★★ | M | SSE + agent | Feels like a colleague |
| 3.3 Diagrams (mermaid) | ★★★ | M | `edges` table | **BUILT** — a second view of 2.2, not a second query |
| 5.3 Incremental re-index | ★★★ | L | Snapshots | Freshness; saves compute |
| 4.2 IDE extension | ★★★★★ | L | The whole API | Adoption, but a new surface |
| 1.1 TypeScript | ★★★★★ | L–XL | Chunking only; resolution is new | Highest ceiling, biggest investment |
| 4.3 PR bot | ★★★ | L | Needs 2.1/2.3 first | Shareable |

---

## Recommended build sequence

A path that front-loads value and defers the big investments, each step
standing on the last:

1. **3.1 Auto-generated overview** — biggest promise-delivery per hour; makes the
   repo page (which you just enhanced) *the* onboarding surface.
2. ~~**2.2 Architecture rollup** + **3.3 diagrams**~~ — **done.** Both fell out of
   the graph, as predicted; 3.3 turned out to need no query at all.
3. ~~**2.1 Git-history tool**~~ — **done.** The endpoint/tool split above was the
   right call and is now the built shape; the semantic-search-over-messages half
   remains unbuilt and is the only part that would need a model.
4. **3.5 explain-this** + **2.4 test↔code** + **6.x product polish** — a cluster of
   cheap wins that make it feel finished.
5. **4.4 multi-turn memory** — the interaction upgrade from "search box" to
   "colleague."
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

*This is a living idea list. Add, cut, and re-rank freely — the point is a clear
menu, not a contract.*
