# HOW-IT-WORKS.md — the whole project, explained simply

> **Who this is for.** Anyone who wants to understand *what this project does and
> how*, without reading the formal spec. Plain language, lots of analogies. If
> you want exact contracts and numbers, read `SPEC.md`; this file is the story
> behind them.

---

## 1. What this project is, in one breath

Imagine you just joined a company and someone hands you a huge, unfamiliar
codebase and says "figure out how the login works." Normally you'd spend days
clicking through files.

**This project is a tool that reads an unfamiliar GitHub repo for you and lets
you ask it questions in plain English — and every answer comes with clickable
links to the exact lines of code it's talking about.**

You paste a repo URL. It goes off and "reads" the whole thing. Then you chat
with it: *"How does retrying work?"* and it replies with a real answer plus
`file:line` citations you can click to jump straight to the source.

That's it. Everything below is *how* it pulls that off.

---

## 2. The two big jobs

The whole system really only does two things:

1. **Ingestion** — read and "understand" the repo, and turn it into something a
   computer can search and reason over. This happens **once per repo**, in the
   background, and can take anywhere from ~30 seconds to a few minutes.
2. **Answering** — when you ask a question, find the relevant code and reason
   over it to produce a cited answer. This happens **every time you ask**.

Think of it like a librarian:
- **Ingestion** = the librarian reads every book and builds a card catalogue.
- **Answering** = you ask a question, and the librarian uses that catalogue to
  find the right pages and explain them to you.

The clever part — and the reason this project exists — is *how* it answers,
which combines two ideas: **RAG** and **agentic AI**. We'll build up to those.

---

## 3. The key ideas, in plain English

Before the flows, here are the building blocks. Each one gets a plain
explanation and an analogy.

### 3.1 Embeddings (turning text into numbers)

A computer can't "understand" the sentence *"how do I retry a request?"* — but
it *can* compare numbers. An **embedding** is a way of turning a piece of text
(or code) into a long list of numbers (a "vector") such that **things with
similar meaning end up with similar numbers.**

**Analogy:** imagine a giant map where every phrase is a dot. *"retry a request"*
and *"attempt the call again after a failure"* land right next to each other,
even though they share no words. *"chocolate cake"* lands far away. Embeddings
are the coordinates of each dot on that map.

In this project, a small AI model (`bge-small`, run via a library called
*sentence-transformers*) turns each chunk of code into one of these vectors.

### 3.2 Vector search (a.k.a. semantic search)

Once everything is a dot on the map, **searching = "find the dots nearest to my
question's dot."** You embed the question, then look for the closest code
vectors. This finds code by *meaning*, not by exact words — so a question about
"retrying" can find code that says "attempt again," even without the word
"retry."

**Analogy:** you drop a pin where your question lands on the map, and grab the
nearest neighbours.

### 3.3 Keyword search (full-text search, "FTS")

The classic kind: match the actual words. If you search `HTTPTransport`, it finds
the places that literally contain `HTTPTransport`. This is great for exact names
(function names, class names) that semantic search can be fuzzy about.

**Analogy:** Ctrl+F, but ranked by relevance.

### 3.4 Hybrid search (using both, then blending)

Semantic search is good at *meaning*; keyword search is good at *exact names*.
Neither wins alone, so this project **runs both and blends the results** using a
simple, well-known recipe called **RRF (Reciprocal Rank Fusion)**: each method
produces a ranked list, and RRF merges them so things ranked high by *both* rise
to the top.

**Analogy:** you ask two experts — one who understands meaning, one who's great
with exact names — and trust the answers they *both* rank highly.

In this project all of this is a **single SQL query** in Postgres (more on the
database later). That's the "retrieval" engine.

### 3.5 AST chunking (cutting code the smart way)

Before you can search code, you have to break it into pieces ("chunks") to embed.
The naive way is to cut every ~1000 characters — but that slices functions in
half.

Instead, this project runs code through a parser (**tree-sitter**) that
understands the code's grammar and produces an **AST (Abstract Syntax Tree)** —
a structured tree of "this is a function, this is a class." It then **cuts along
those natural seams**, so each chunk is a *whole* function, method, or class.

**Analogy:** slicing a cake along the layers, not straight through the middle of
each slice.

*Why it matters:* whole units make for clean citations (a citation points at a
complete function, not a random window), cleaner search, and — crucially — they
let us build the symbol graph next.

### 3.6 The symbol graph (a map of who-calls-whom)

The AST tells you *what* each piece is. The **symbol graph** tells you *how the
pieces connect*: which function **calls** which, which file **imports** what,
which class **extends** which.

It's stored as two tables: **symbols** (the definitions — the "dots") and
**edges** (the relationships — the "arrows"). The dots come from the AST; the
arrows come from a tool called **Jedi** that figures out "this `connect()` call
refers to *that* specific `def connect` over there."

**Analogy:** a subway map. Stations = functions. Lines between them = "this one
calls that one."

*Why it matters:* it's the project's secret weapon, and section 5 explains why.

### 3.7 RAG (Retrieval-Augmented Generation)

Left to itself, a language model answers from vague memory and often makes things
up ("hallucinates"), especially about *your specific* code, which it has never
seen.

**RAG fixes this by doing retrieval *first*: fetch the relevant real code, hand
it to the model, and ask it to answer *using that*.** The model stops guessing
and starts answering from actual facts you gave it.

**Analogy:** the difference between a closed-book exam (answer from memory,
error-prone) and an **open-book exam** (look up the real page, then answer). RAG
turns every question into an open-book exam.

In this project, "retrieval" = the hybrid search from 3.4.

### 3.8 Agentic AI (the model that takes actions in a loop)

Plain RAG does *one* retrieve, then *one* answer. But real questions often need
*several* steps: search, read a file, look at what a function calls, search
again. A single retrieve isn't enough.

**An "agent" is a language model that can take actions — call *tools* — and
decide its own next step, in a loop, until it has enough to answer.** It's not a
one-shot responder; it's a little problem-solver.

**Analogy:** a detective. It doesn't solve the case in one glance — it follows a
lead, examines a clue, follows the next lead, and only then writes up the
conclusion. But it's given a **budget of 8 moves**, after which it must stop and
answer (so it can never loop forever).

In this project the agent has **six tools** (its "detective abilities"):

| Tool | What it does (plainly) |
|---|---|
| `search_code` | The hybrid search — "find code related to X." (This is the RAG step.) |
| `read_file` | Open a file and read a range of lines. |
| `get_definition` | "Where is this thing defined?" (walks the symbol graph) |
| `find_references` | "What uses this thing?" (walks the symbol graph) |
| `expand_context` | "Pull in the nearby connected code." (walks the graph) |
| `list_directory` | "What files/folders are here?" (get the lay of the land) |

The framework that runs this loop is called **LangGraph**.

### 3.9 Citations (every claim comes with a receipt)

A rule of this project: **an answer without citations is a bug.** Every answer
points at `file:start-end` line ranges. Click one and the exact code opens on the
right, scrolled to and highlighted.

**Analogy:** a research paper where every sentence has a footnote you can check.

### 3.10 Streaming (SSE — watching it work live)

You don't wait in silence. As the agent works, you **watch its steps appear
live** ("searching… reading file… traversing the graph…"), then the answer
**types itself out** word by word. This live feed uses a web technology called
**SSE (Server-Sent Events)** — a one-way stream from the server to your browser.

**Analogy:** watching someone show their work on a whiteboard in real time,
instead of getting a finished page slid under the door.

---

## 4. The ingestion flow (how we "read" a repo)

This runs in the background when you submit a repo. Step by step:

```
GitHub URL
   │
   ▼
1. CLONE          → download a shallow copy of the repo
   │
   ▼
2. FILTER         → throw out what we don't want: .git, node_modules, huge
   │                 files, binaries, non-code (rules in filters.py)
   ▼
3. PARSE & CHUNK  → tree-sitter builds the AST; cut into whole functions /
   │                 classes; attach a little header (file path, name,
   │                 signature, docstring, imports) so each chunk is
   ▼                 self-describing
4. EMBED          → turn each chunk into a vector (bge-small model)
   │
   ▼
5. SYMBOL GRAPH   → record every definition (symbols) and resolve the
   │                 call/import/extends links between them with Jedi (edges)
   ▼
6. STORE          → save everything in Postgres: chunk text + its vector +
                     searchable text, plus the symbols and edges
```

A few plain-language notes:

- **It's a background job.** Cloning + embedding a repo takes minutes, and no web
  request can wait that long. So a separate **worker** process does the heavy
  lifting and writes its **progress** to the database as it goes
  (`cloning → parsing → linking → embedding → ready`). The website just *polls*
  that progress and shows you the moving bars — which is exactly the status page
  you see.
- **Test code is tagged.** Chunks that are tests get a flag (`is_test`) and are
  *excluded* from search by default — they were drowning out the real
  implementation for natural-language questions.
- **It happens once.** A repo at a specific commit is stored as an immutable
  "snapshot." Ask a hundred questions later; it's already indexed.

At the end, the repo is now a searchable pile of code chunks *plus* a map of how
they connect. Ready for questions.

---

## 5. The answering flow — where RAG **and** the agent work together

This is the heart of the project, so we'll go slow. Here's the whole loop, then
the explanation:

```
Your question: "How does retrying work?"
   │
   ▼
1. The agent starts (a language model, e.g. Mistral)
   │
   ▼
2. It calls search_code  ───►  HYBRID SEARCH (this is the RAG step):
   │                            embed the question, find the nearest code
   │                            chunks by meaning + keyword, blend with RRF,
   │                            return the top matches
   ▼
3. The agent reads what came back and THINKS:
   "The match is in HTTPTransport, but the retry logic is passed into a
    connection pool I can't see yet. Let me follow the thread."
   │
   ▼
4. It calls graph tools  ───►  get_definition / find_references / expand_context
   │                            walk the SYMBOL GRAPH to pull in the connected
   │                            code that search did NOT surface
   ▼
5. Maybe it searches or reads again... (looping, up to 8 tool calls total)
   │
   ▼
6. It has enough. It writes the final answer, grounded in the real code it
   gathered, with file:line CITATIONS — and the whole thing STREAMS to your
   screen as it happens.
```

### The one insight that explains this whole project

> **Retrieval (RAG) finds the *entry point*. The graph lets the agent find the
> *rest of the answer*.**

Here's why that matters. Plain search is great at finding the chunk whose words
match your question. But the *actual answer* often lives in code that shares **no
words** with your question — a helper function three calls deep, or a base class
in another file. Search is blind to those.

So this project does **agentic RAG**:

- **RAG part:** the `search_code` tool fetches the best-matching chunks — the
  "front door."
- **Agent part:** the model then *decides* it needs more, and **walks the symbol
  graph** (via `get_definition`, `find_references`, `expand_context`) to gather
  the connected code retrieval missed — walking the building behind the door —
  and can loop, retrieving and reading several times before answering.

Plain RAG = *one* search, *one* answer. This = a **detective that searches, then
follows the code's own connections** until it can answer for real. That
combination — retrieval to get in, graph traversal to go deep — is the thing a
normal "search box with AI" fundamentally cannot do.

### An honest note (the project measures itself)

The team was careful to check whether the graph *actually* helps. The honest
finding: it demonstrably reaches at least one question that no search alone ever
answers — but the size of the benefit is **moderate** and **depends on which
model** does the reasoning (it helped clearly on one model family, less on
another). So the graph is a real edge, with an asterisk — not magic. (Details in
`CLAUDE.md` and `EVAL.md`.)

---

## 6. The moving parts (the architecture)

The system is a few separate programs, each with one job. Here's why it's split
up:

```
        ┌──────────────┐        you type a URL / a question
        │   Frontend   │  ◄──────────────────────────────────────┐
        │ (the website)│                                          │
        └──────┬───────┘                                          │
               │  HTTP + a live SSE stream                        │
               ▼                                                  │
        ┌──────────────┐   "please index this repo"        ┌──────┴──────┐
        │     API      │ ─────────────────────────────────►│    Redis    │
        │  (FastAPI)   │      (drops a job on the queue)    │  (job queue)│
        └──────┬───────┘                                    └──────┬──────┘
               │                                                   │ picks up job
               │ reads/writes                                      ▼
               ▼                                            ┌──────────────┐
        ┌──────────────┐   the ingestion flow (section 4)  │    Worker    │
        │   Postgres   │ ◄─────────────────────────────────│ (does the    │
        │  (database)  │      writes chunks, vectors,       │  heavy work) │
        └──────────────┘      symbols, edges, progress      └──────────────┘
```

- **Frontend** (Next.js website) — what you see. Submit a repo, watch progress,
  chat, click citations. Talks to the API.
- **API** (FastAPI, Python) — the front desk. It answers fast requests. It never
  does slow work itself; when you submit a repo it just **drops a job on the
  queue** and returns immediately. When you chat, it runs the agent and
  **streams** the result back.
- **Worker** (ARQ) — the back room. It picks jobs off the queue and runs the
  whole minutes-long ingestion flow, writing progress as it goes. This is why the
  website can stay responsive while a repo indexes.
- **Redis** — the job queue (a fast in-memory store). The API puts jobs in; the
  worker takes them out.
- **Postgres** (with an add-on called **pgvector**) — the one database that holds
  *everything*: chunk text, the vectors (pgvector makes vector search possible
  inside SQL), the searchable text, the symbols and edges, plus users and
  snapshots.

**Why separate the worker from the API?** Because you can't make a web request
sit and wait five minutes for a repo to embed. So the slow part runs elsewhere,
reports progress to the database, and the website polls it. This is the single
most important structural decision — if the worker isn't running, a submitted
repo just sits at 0% forever.

---

## 7. The tech stack, one plain line each

| Piece | What it is | Why it's here |
|---|---|---|
| **Python / FastAPI** | The backend language + web framework | Runs the API and the worker |
| **Next.js** | A React website framework | The frontend you click around in |
| **tree-sitter** | A code parser | Builds the AST so we can chunk on real boundaries |
| **Jedi** | A Python code-analysis tool | Figures out the call/import links for the symbol graph |
| **sentence-transformers (`bge-small`)** | A small embedding model | Turns code chunks and questions into vectors |
| **Postgres + pgvector** | A database + a vector add-on | Stores everything and does hybrid search in one query |
| **Redis + ARQ** | An in-memory store + a job runner | The background-job queue |
| **LangGraph** | An agent framework | Runs the tool-calling loop (the "detective") |
| **The model** (Mistral by default; Gemini/Claude/Vertex optional) | The language model that reasons and writes answers | Configurable — you pick the provider with one setting |
| **SSE (sse-starlette)** | A live server→browser stream | Lets you watch the steps and the answer appear live |

---

## 8. A full example, start to finish

Let's follow one real journey:

1. **You paste** `https://github.com/encode/httpx` and hit *Index repo*.
2. The API drops an **ingest job** on Redis and sends you to the status page.
3. The **worker** picks it up and runs the ingestion flow: it clones httpx,
   filters junk, parses every Python file into an AST, cuts ~1,500 whole-unit
   chunks, embeds them, builds the symbol graph (~1,200 symbols, ~2,300 edges),
   and stores it all — writing progress the whole time. The bars move
   `queued → cloning → parsing → linking → embedding → ready`.
4. You land on the chat and ask: **"How does httpx decide which transport to
   use?"**
5. The **agent** starts. It calls **`search_code`** (the RAG step) and gets back
   the chunks that best match "transport" by meaning and by name.
6. It sees the answer depends on a method it hasn't looked at, so it calls
   **`get_definition`** and **`read_file`** — walking the graph and reading the
   real lines. (You watch each step stream in live.)
7. Within its 8-move budget, it has enough. It **writes the answer**, streaming
   word by word, ending with **citation chips** like `httpx/_client.py:718-738`.
8. You **click a citation**. The right pane opens that file, scrolls to line 718,
   and highlights the range — so you can verify the answer against the real code
   yourself.

That's the entire product, one loop.

---

## 9. The mental model to keep

If you remember nothing else:

- **Ingestion** turns a repo into two things: a **searchable pile of whole-unit
  code chunks** (for RAG) and a **map of how the code connects** (the symbol
  graph).
- **Answering** is a **detective (the agent)** that uses **RAG to find the front
  door**, then **walks the symbol graph to explore behind it**, looping through a
  handful of tools, and finally writes a **cited** answer that **streams** to
  your screen.
- Everything is split into an **API** (fast), a **worker** (slow background
  jobs), a **database** (Postgres, holds it all), and a **website** — because
  reading a whole repo takes minutes and can't block a web request.

RAG gets it in the door. The agent and the graph are what make it actually
understand the house.

---

*Want the exact schemas, algorithms, and tool signatures behind all of this?
That's what `SPEC.md` is for. Want the design decisions and the honest
measurements? See `DECISIONS.md` and `EVAL.md`.*
