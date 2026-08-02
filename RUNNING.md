# RUNNING.md — set up and run the project

Everything here was executed end-to-end on a clean clone on **2026-07-27**, and
the timings are measured, not estimated. Where a number depends on a cold cache
that this run did not have, it says so.

`README.md` has the short version. This file is the long version: every command,
what each one should print, what to do when it doesn't, and how to tear it down.

---

## 0. What you need

| | version | check | notes |
|---|---|---|---|
| Python | 3.11+ | `python3 --version` | |
| [uv](https://docs.astral.sh/uv/) | latest | `uv --version` | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Node | 20.9+ | `node --version` | |
| pnpm | 10+ | `pnpm --version` | `corepack enable && corepack prepare pnpm@latest --activate` |
| Docker | any recent | `docker info` | must show a running daemon, not just a version |
| Disk | ~7 GB free | `df -h .` | the Python venv alone is 5.3 GB (torch) |
| RAM | 8 GB+ | `free -h` | see the note below |

**On RAM, precisely:** all measurements in this repo were taken on a 15 GB /
4-CPU box with no GPU. An 8 GB host loaded torch fine but swap-thrashed to a
standstill on the 2.4 GB *reranker* — which is off by default, so 8 GB is
expected to be workable for the shipped configuration. That expectation is
untested; only the 15 GB box is on the record.

You also need **one model API key**. The default provider is Mistral
(`console.mistral.ai` — the free tier needs phone verification). It is the
provider every measured number in this repo was produced on.

> **Why Mistral and not a bigger name:** free tiers were compared by *tokens per
> day*, not requests per day. Google AI Studio allows 20 requests/day/model —
> about two agent runs — which is smoke-test only. An agent loop needs token
> headroom. See `docs/DECISIONS.md`, "Provider roles".

---

## 1. Infrastructure

From the repo root:

```bash
docker compose up -d --wait
```

Brings up Postgres 16 (with pgvector) and Redis, and blocks until both report
healthy. **Measured: 13 s** from a cold volume.

Expected tail:

```
 Container r1-redis-1     Healthy
 Container r1-postgres-1  Healthy
```

If it hangs or fails, the daemon usually isn't running — `docker info` should
print server details, not an error.

---

## 2. Configuration

```bash
cd backend
cp .env.example .env
```

Then open `backend/.env` and set exactly one value:

```
MISTRAL_API_KEY=<your key>
```

**Nothing else needs changing.** `DATABASE_URL` and `REDIS_URL` in
`.env.example` already match `docker-compose.yml` — verified, not assumed:

```
DATABASE_URL=postgresql://app:app@localhost:5432/codebase_assistant
REDIS_URL=redis://localhost:6379
```

`backend/.env` is gitignored and never travels with the repo. All configuration
is read through `app/config.py` (pydantic-settings) — there is no `os.environ`
access anywhere else, so anything you set here is the single source of truth.

<details>
<summary>The rest of the environment variables, and when you'd touch them</summary>

| Var | Default | When you'd change it |
|---|---|---|
| `AGENT_MODEL` | `mistral-medium-latest` | switch provider; the prefix selects it (`gemini` / `claude` / `vertex:`) |
| `EMBEDDING_MODEL` | `BAAI/bge-small-en-v1.5` | never, unless you re-ingest everything |
| `RERANKER_MODEL` | `BAAI/bge-reranker-v2-m3` | only if you re-enable the reranker (it is ablated — see §7) |
| `FRONTEND_ORIGIN` | `http://localhost:3000` | your browser is on a different origin (see §6) |
| `FRONTEND_ORIGIN_REGEX` | unset | local work behind a port-forwarding editor |

Changing `EMBEDDING_MODEL` invalidates every stored vector. Re-ingest after.
</details>

---

## 3. Dependencies and database

```bash
# still in backend/
uv sync
uv run python scripts/migrate.py
```

`uv sync` on a **cold** uv cache takes **~6 minutes** — it builds torch, which is
most of the 5.3 GB venv. On a warm cache it is seconds (measured 6.9 s), so
don't be alarmed by either extreme.

`migrate.py` should print:

```
apply  001_init.sql (version 1)
apply  002_files_chunks.sql (version 2)
apply  003_is_test.sql (version 3)
apply  004_symbols.sql (version 4)

done: 4 applied, 0 skipped
```

**Measured: 2.4 s.** It is idempotent — run it again and every line becomes
`skip`, `0 applied, 4 skipped`. Safe to re-run any time; that is how you apply
new migrations later.

---

## 4. Run it — three processes

**All three are required.** Without the worker, a submitted repo enqueues a job
nobody runs, progress sits at 0% forever, and there is no error message to tell
you why. This is the single most common way to have a broken-looking setup.

```bash
# terminal 1 — API on :8000
cd backend && uv run uvicorn app.main:app --reload

# terminal 2 — the worker (NOT optional)
cd backend && uv run arq app.worker.WorkerSettings

# terminal 3 — UI on :3000
cd frontend && pnpm install && pnpm dev
```

**The API's first start takes ~30 seconds** (measured: 29 s) while the embedding
model loads, and the port refuses connections the whole time. That is normal,
not a hang. On the very first run ever it also *downloads* that model (~130 MB)
before the 30 s — budget a few minutes more on a genuinely cold machine.

Both the API and the worker load the embedder at startup: the API because every
chat request runs `search_code`, the worker because it embeds. `pnpm install` is
~12 s, `pnpm dev` is ready in ~2 s.

Sanity check while you wait:

```bash
curl -s localhost:8000/health     # {"ok": true}
```

`/health` answers immediately even with Postgres and Redis down — an unreachable
dependency is a logged warning plus a 503 from the endpoint that needs it, never
a boot failure.

---

## 5. Use it

Open **http://localhost:3000** and submit a repo.

**Start with [`pallets-eco/blinker`](https://github.com/pallets-eco/blinker)** —
measured **28.6 s** end to end (7 files, 76 chunks). Note the org is
`pallets-eco`, not `pallets`; `pallets/blinker` 404s.

`encode/httpx` is the benchmark repo and takes **about 8 minutes**, most of it
embedding, printing little while it works. Fine to leave running; not a good
first test.

You should see, in order:

1. the status page walk `queued → cloning → parsing → linking → embedding → ready`
   with progress bars moving
2. a chat CTA once it's ready
3. on asking a question: a live tool-call timeline, then the answer streaming in
4. citation chips under the answer — click one and the right pane loads the file,
   scrolls to the range, and washes those exact lines

A verified example: asking *"How does blinker connect a receiver to a signal?"*
returned in ~21 s with 2 tool calls (`search_code`, `read_file`) and 5 validated
citations; clicking `src/blinker/base.py:91-115` washed exactly lines 91–115,
landing on `def connect(self, receiver: F, ...)`.

**An answer without citations is a bug, not a degraded mode.**

### Without the web app

```bash
cd backend
uv run python -m app.ingest.cli https://github.com/encode/httpx --db
uv run python -m app.agent.cli https://github.com/encode/httpx \
  "How does httpx pick a transport?"
```

The agent CLI streams the tool timeline, then prints the answer, the tool-call
count, and validated citations. Useful flags: `--json` for machine-readable
output, `--tool-cap N` to change the 8-call cap. On the ingest side: `--dump
PATH` writes every chunk as JSONL, `--sample N` prints N random full chunks.

---

## 6. When it doesn't work

| Symptom | Cause | Fix |
|---|---|---|
| Progress stuck at 0%, no error | **the worker isn't running** | start terminal 2 |
| Progress stuck at 0%, and the worker terminal shows `redis.exceptions.TimeoutError` or `ConnectionError: Connection reset by peer` then exits | a managed Redis is slower to connect than ARQ's 1s default, and a mid-command reset was not retried at all | defaults now cover both (`REDIS_CONN_TIMEOUT_S=10`, `REDIS_COMMAND_RETRIES=3`); if it still happens, raise them in `backend/.env` and restart the worker |
| `curl localhost:8000/health` refuses connection for ~30 s | embedder loading | wait; it's normal |
| Every request fails with a CORS error | browser is on an origin the API doesn't allow — common when an editor forwards `:3000` elsewhere | set `FRONTEND_ORIGIN` in `backend/.env` to the exact origin in your address bar, or use `FRONTEND_ORIGIN_REGEX` |
| 503 from `/repos` | Redis unreachable | `docker compose up -d --wait` |
| 422 on submit | non-GitHub or malformed URL | the API only accepts `https://github.com/owner/repo` |
| 409 on chat | repo isn't `ready` yet | wait for the status page |
| Clone fails on a real repo | v1 is **public repos only** | private repos are v2 |
| Ingest finishes with 0 chunks | repo has no Python | v1 is **Python only** |
| OOM / swap thrash | <8 GB RAM | the embedder needs headroom |

Logs worth reading: the worker terminal narrates every ingest stage; the API
terminal logs each request and any typed exception.

---

## 7. Tests

```bash
cd backend  && uv run pytest && uv run ruff check . && uv run mypy app
cd frontend && pnpm build && pnpm lint && pnpm test
```

Measured on a clean clone: **163 backend tests pass** in ~2m20s (the integration
tests need the compose Postgres and Redis up, and skip cleanly if they aren't),
ruff and mypy clean; frontend build clean, lint clean, **16 vitest tests pass**.

### Reproducing the measured numbers

```bash
cd backend
uv run python scripts/eval.py --mode vector,fts,hybrid       # ~1 min
uv run python scripts/debug_search.py "<query>"              # per-signal ranks
```

`eval.py` appends a dated block to `docs/EVAL.md`; it only measures and never
tunes. **Avoid `--mode all`** unless you want the cross-encoder: that mode is a
CPU reranker and turns a one-minute run into 20–50 minutes. It is off by default
because it measured *worse-or-equal* to plain RRF fusion at every k and at MRR —
the ablation stays wired so it remains measurable, not because it helps.

Two things to know before you touch retrieval: test chunks are flagged at ingest
and **excluded by default** (they were systematically outranking implementation
for natural-language questions — 46% of the benchmark corpus), and the benchmark
in `docs/EVAL.md` is **frozen**. Never tune against an individual question.

---

## 8. Shutting down

```bash
# Ctrl-C each of the three terminals, then:
docker compose down          # keeps the database volume
docker compose down -v       # deletes it too — full reset
```

Use `-v` when you want a genuinely clean slate; the next `migrate.py` will
rebuild the schema from scratch. If you put a real API key in `backend/.env` on
a shared or temporary machine, delete that file when you're done.

---

## 9. Where to read next

| File | What |
|---|---|
| `README.md` | what this is, the measured comparison, the honest scope of the claim |
| `docs/SPEC.md` | schema, chunk format, retrieval algorithm, tool signatures, API contracts |
| `docs/DECISIONS.md` | every architectural choice and why — including two retracted claims |
| `docs/ROADMAP.md` | phases, done-when criteria, v2 backlog |
| `docs/EVAL.md` | the frozen 20-question benchmark and every measurement run |
| `docs/DEPLOY.md` | the cloud path — written, followable, **never executed** |
| `CLAUDE.md` | hard rules and conventions if you're going to write code here |

**Scope of v1:** public GitHub repos, **Python only**, single user, no auth.
TypeScript, private repos, and commit-history indexing are deliberately v2.
