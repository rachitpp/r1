# Phase 4 prompt — API & worker

> **How to use:** save as `docs/prompts/phase-4.md`, start a fresh
> Claude Code session at the repo root (Sonnet is sufficient — this
> phase is mechanical wiring against frozen contracts), and instruct:
> "Read docs/prompts/phase-4.md completely, confirm Phase 3 is done in
> ROADMAP.md, give me a ≤10-line plan, then proceed." Single phase,
> single report at the end — no milestones.

---

You are starting **Phase 4 — API & worker**: everything Phase 3 does,
over HTTP, with ingestion as a proper background job. The contracts are
already frozen (SPEC §8 API, §9 SSE events, §10 job lifecycle). No
design decisions remain — implement the spec.

## Step 0 — Orient

Read, in order:
1. `CLAUDE.md`
2. `docs/ROADMAP.md` — Phase 4 section
3. `docs/SPEC.md` — §7.5, §8, §9, §10, §12
4. `docs/DECISIONS.md` — Phase 3 FINAL RESULT + shipped config
5. `docs/HANDOFF.md` — Phase 4 next steps

Confirm Phase 3 is `done`. Plan in ≤10 lines, then proceed.

## Step 0.5 — Gate: Redis reachable

Phase 4 is where Redis becomes real (ARQ queue). Check connectivity
using `REDIS_URL` from config (an arq `create_pool` + ping is enough).

If unreachable → **STOP** and tell the human the two options:
- **Upstash free tier (recommended — matches the Neon pattern):**
  create a database at upstash.com, put the TLS URL
  (`rediss://default:...@....upstash.io:6379`) in `backend/.env`.
- Local `docker compose up -d redis` if Docker is available in this
  environment.

Do not mock the queue or fall back to in-process execution.

## Session rules

- Build **only Phase 4**: no UI, no frontend code, **no WebSockets**,
  and **no refactoring of the agent** — the chat endpoint *adapts* the
  existing `astream_events` stream into §9 SSE events; it does not
  restructure the graph, prompts, or tools.
- Pre-authorized new dep: `sse-starlette`. Nothing else without asking.
- Shipped agent config is frozen (DECISIONS): `mistral-medium-latest`,
  temperature 0, `AGENT_TOOL_CAP=8`, `RERANK_ENABLED=false`. Do not
  touch retrieval or agent parameters. No eval runs this phase — the
  frozen benchmark is not exercised here.
- **ARQ on Upstash budget rule:** set the worker's poll delay to ~2s
  (default 0.5s polling ≈ 170K commands/day — it would exhaust
  Upstash's free monthly command budget in days if a worker ran 24/7).
  Log the math in DECISIONS as a Phase 6 deploy consideration.
- ruff + mypy green per commit; small logical commits.

## Reconciliations (do early, log each in DECISIONS.md)

1. **`linking` status.** The ingest pipeline gained the Phase 3 symbol
   pass (runs after parsing, while the clone is on disk). Extend the
   §10 state machine: `queued → cloning → parsing → linking →
   embedding → ready | failed`. Update SPEC §10 and the §3 status
   comment. (The frontend doesn't exist yet — this is the cheap moment
   to widen the enum.)
2. **Shared ingest function.** Extract the CLI `--db` pipeline into one
   `run_ingest(repo_id)` that both the CLI and the ARQ task call —
   clone → filter → parse → **symbols** → embed → backfill, with
   delete-and-replace at the start (§10 idempotency) and workdir
   cleanup in `finally`. The CLI becomes a thin wrapper. (Refactoring
   *ingest* is expected; the agent stays untouched.)
3. **Embedder warm-up in the API process.** SPEC §4 says models load
   once per process at startup; Phase 2's lazy-load was an 8 GB-host
   workaround. The API lifespan now warms the embedder (search_code
   needs it per chat request; first-question latency should not pay
   the load cost). Lazy behavior may remain for CLI/test paths.
   Supersede the old note in DECISIONS.

## Deliverables

### 1. ARQ ingest task (`app/worker.py`)
- `ingest_repo(ctx, repo_id)` calling `run_ingest`, writing progress
  to the repo row at each state transition and every `PROGRESS_EVERY_N`
  units (files parsed, chunks embedded), per §10.
- Failure captured into `repos.error` with status `failed`;
  `job_timeout=900`, `max_tries=2`; retry re-enters cleanly via
  delete-and-replace.
- **Zombie sweep** on worker startup: any repo in an in-flight state
  with `updated_at` older than `ZOMBIE_AFTER_S` → `failed("worker
  died")`.
- Worker settings: poll delay ~2s (budget rule above).

### 2. HTTP API (`app/api/`)
Exactly per SPEC §8 — routes thin, logic in services, Pydantic v2
schemas in `app/api/schemas.py`, typed exceptions mapped to HTTP only
in the api layer:

- `POST /repos` `{url}` → validate GitHub URL → create row or return
  existing by unique url (201 / 200) → **enqueue** `ingest_repo` →
  `RepoOut`. 422 invalid URL.
- `GET /repos` → list; `GET /repos/{id}` → `RepoOut` with progress;
  404 unknown.
- `GET /repos/{id}/files?path=...` → `{path, content, n_lines}` from
  the files table; 404 unknown repo or path. (Powers Phase 5's viewer
  and citation clicks.)
- `POST /repos/{id}/chat` `{question}` → §9 SSE stream via
  sse-starlette. 409 `{detail, status}` if repo not `ready`; 404
  unknown repo.
- `GET /health` unchanged.
- **CORS middleware** now: allow origin from a new optional config
  field `FRONTEND_ORIGIN` (default `http://localhost:3000`) — saves
  Phase 5 a debugging session.

### 3. SSE adaptation (`app/api/chat_stream.py` or similar)
Map the agent's `astream_events` output onto the §9 schema, in order:
`status(thinking)` → interleaved `tool_call` / `tool_result` / `text`
deltas → `citations` → `done(tool_calls_used)`; `error` on failure.
Rules from §9: `tool_result` payloads carry **summaries and locations
only — never full code bodies over the wire**; citations come from the
existing Phase 3 parser (§7.5) run over the final answer, validated
against the files table; the 8-cap is enforced server-side by the
existing graph (do not duplicate the cap in the transport layer).

### 4. Tests
- API tests via httpx ASGITransport, **networkless**: override the
  model factory with the Phase 3 scripted fake model (fixture-level
  dependency override) so chat tests exercise the full SSE pipeline —
  assert the event *sequence* (status → tool_call → tool_result → text
  → citations → done) and the no-code-bodies rule on tool_result.
- Route tests: happy path, unknown repo (404), chat-before-ready
  (409), invalid URL (422), files endpoint (200/404).
- Worker: unit test the zombie sweep (insert a stale in-flight row →
  start sweep → row failed). Integration test (marked, skipped when
  Redis/DB unreachable): enqueue ingest of the tiny fixture repo, run
  the worker inline (arq's test utilities or a one-shot run), assert
  the row reaches `ready` with correct counts; enqueue again, assert
  idempotent re-ingest.

## Verification — run and paste the transcript

```bash
cd backend
uv run pytest && uv run ruff check . && uv run mypy app
# terminal 1:
uv run uvicorn app.main:app --port 8000
# terminal 2:
uv run arq app.worker.WorkerSettings
# terminal 3 — the full flow, against a real small repo:
curl -s -X POST localhost:8000/repos \
  -H 'content-type: application/json' \
  -d '{"url": "https://github.com/encode/httpx"}'
# poll until ready (expect queued→cloning→parsing→linking→embedding→ready):
curl -s localhost:8000/repos/<id>
# then stream a chat and watch §9 events arrive:
curl -N -s -X POST localhost:8000/repos/<id>/chat \
  -H 'content-type: application/json' \
  -d '{"question": "How does httpx decide which transport to use?"}'
# error paths:
curl -s localhost:8000/repos/00000000-0000-0000-0000-000000000000   # 404
curl -s -X POST localhost:8000/repos/<id>/chat -d '{"question":"x"}' \
  -H 'content-type: application/json'   # 409 while still indexing
```

**Manual resilience check (report the outcome):** start an ingest, kill
the worker mid-run (Ctrl-C during embedding), restart it — the job must
retry cleanly or the row must land in `failed` with an error; no row
may be left stuck in an in-flight state (the sweep catches stragglers).

Since httpx is already ingested from Phase 3, use a fresh delete or a
second small public repo if needed to demonstrate a clean end-to-end
ingest through the queue — note which you did.

## Wrap up

1. ROADMAP Phase 4 → done, boxes ticked with the curl transcript as
   evidence.
2. DECISIONS: the three reconciliations + the Upstash budget note.
3. HANDOFF: Phase 4 outcome, how to run api+worker, Phase 5 next.
4. Final commit. ≤10-line summary including one paste-ready SSE
   excerpt (a few events) from the live chat stream.