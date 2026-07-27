# Phase 5 prompt — Frontend

> **How to use:** save as `docs/prompts/phase-5.md`, start a fresh
> Claude Code session at the repo root (Sonnet is sufficient), and
> instruct: "Read docs/prompts/phase-5.md completely, confirm Phase 4
> is done in ROADMAP.md, give me a ≤10-line plan, then proceed."
> **The backend must be running for this phase** (API + worker, two
> terminals, per HANDOFF "Running the stack") — verification is done
> live in a browser.

---

You are starting **Phase 5 — Frontend**: the demo layer. The backend
contract is frozen and live (§8 endpoints, §9 SSE events); nothing
here invents protocol. The point of this UI is **showing the agent's
work** — the tool-call timeline streaming in real time is the hero
element, and citation-click-to-highlighted-code is the payoff.

## Step 0 — Orient

Read, in order:
1. `CLAUDE.md` — especially the frontend conventions
2. `docs/ROADMAP.md` — Phase 5 section
3. `docs/SPEC.md` — §8, §9
4. `docs/HANDOFF.md` — "Immediate next steps", "Running the stack",
   and the Phase 3/4 gotchas
5. `docs/DECISIONS.md` — shipped agent config

Confirm Phase 4 is `done`. Plan in ≤10 lines, then proceed.

## Step 0.5 — Gate: live backend

```bash
curl -s localhost:8000/health          # {"ok": true}
curl -s localhost:8000/repos           # httpx row present, status "ready"
cd frontend && pnpm install
```

If the API is down or no `ready` repo exists → **STOP** and tell the
human to start the stack per HANDOFF. Do not mock the API.

## Session rules

- Build **only the frontend** — with exactly **one pre-authorized
  backend exception**: `POST /repos` on an existing row whose status is
  `failed` re-enqueues the ingest job and returns 200 (a small service
  tweak + one test). Without it a failed repo is bricked forever with
  no UI recourse. **Nothing else in `backend/` changes.**
- Pre-authorized frontend deps: `@tanstack/react-query`, `shiki`,
  `vitest` (dev, for lib tests), plus shadcn component additions via
  its CLI (Card, Input, Badge, Skeleton, Button variants, etc.).
  **No Vercel AI SDK** — see Reconciliation. **No WebSockets.** Nothing
  else without asking.
- CLAUDE.md conventions hold: server components by default,
  `"use client"` only where interaction requires it; chat state in the
  custom hook, all other server state via TanStack Query; no
  Redux/Zustand.
- The frozen benchmark is not touched this phase. No agent/retrieval
  config changes.
- `pnpm build` and `pnpm lint` green per commit; `vitest` green.

## Reconciliation (do first, log in DECISIONS.md)

**Replace the planned `useChat` with a custom hook.** CLAUDE.md's stack
row predates the §9 schema freeze; the AI SDK's stream protocol is not
ours, and adapting it means a translation layer for zero benefit when
§9 maps 1:1 onto UI state. Build `useRepoChat(repoId)` instead:
- `lib/sse.ts` — a small hand-rolled SSE parser over
  `fetch` + `ReadableStream` (handles `event:`/`data:` lines, multi-line
  data, buffering across chunks). Unit-tested with vitest.
- The hook POSTs `{question}` to `/repos/{id}/chat`, dispatches §9
  events into typed state: `steps[]` (tool_call/tool_result pairs),
  `answer` (accumulated text), `citations[]`, `status`
  (idle | thinking | streaming | done | error), `toolCallsUsed`.
- **Delta rule from HANDOFF:** Mistral sends ~70 token-level `text`
  deltas; a non-streaming provider sends one `text` event carrying the
  whole message. Accumulate — never assume one delta is one answer.
- Update CLAUDE.md's stack row; DECISIONS entry with this rationale.

## Deliverables

### 1. Foundations
- `lib/api.ts` — typed client for §8: `RepoOut`, list/get/create repo,
  `getFile(repoId, path)`. Base URL from `NEXT_PUBLIC_API_URL`
  (default `http://localhost:8000`); `frontend/.env.local.example`
  committed, `.env.local` gitignored.
- `lib/citations.ts` — parse/format `[path:start-end]` chips from
  answer text + the `citations` event (dedupe). Unit-tested.
- App-level `QueryClientProvider` (client boundary in the layout).

### 2. `/` — repo list + submit
- List from `GET /repos` (TanStack Query): name, status badge,
  progress hint, click-through to `/repos/[id]`.
- Submit form: POST, on success route to the new repo's page; 422
  renders an inline field error. Existing-URL 200 just routes to the
  existing row.

### 3. `/repos/[id]` — indexing progress
- Poll `GET /repos/{id}` with `refetchInterval` ~1500ms **while
  in-flight**; stop polling on `ready`/`failed`.
- Render **all** states: `queued → cloning → parsing → linking →
  embedding → ready | failed` (the `linking` state exists — HANDOFF).
  Progress bars: files_parsed/files_total, chunks_embedded/chunks_total.
- `ready` → prominent "Ask about this codebase" CTA →
  `/repos/[id]/chat`. `failed` → the error message + a Retry button
  (the pre-authorized re-enqueue).

### 4. `/repos/[id]/chat` — the split pane
**Left — conversation.**
- Transcript of user questions and agent answers; composer disabled
  while a stream is live; 409 (repo regressed to not-ready) handled
  with a friendly redirect to the status page.
- While streaming: a "thinking" indicator on `status`, then the
  **step timeline** rendering live from `tool_call`/`tool_result`
  events — step number, tool name, compact args summary, result
  summary, and **location chips** from `tool_result.locations`
  (remember §9: these events carry *no code bodies* — chips fetch code
  on click via `/files`, never expect it inline).
- The answer streams token-by-token below the steps; on `done`, show
  a subtle "N tool calls" note; `citations` render as chips under the
  answer. `error` events render as an inline error bubble with the
  message.
- Transcript persisted to `sessionStorage` keyed by repo id, so a
  refresh restores it (an in-flight answer is cut on refresh — that is
  accepted and noted; the app must remain fully usable after refresh).

**Right — code viewer.**
- Shiki with the **fine-grained bundle**: Python grammar only, one
  light + one dark theme, a singleton highlighter created once
  (bundle-size discipline — do not ship every language).
- File content via `/files` through TanStack Query (cached per
  repo+path). Line numbers rendered as per-line elements with a
  `data-line` attribute.
- Clicking any citation chip (answer chips or step location chips)
  loads that file, **scrolls to the start line, and highlights the
  start–end range**. Off-by-one care: lines are 1-based end-inclusive.
- States: skeleton while loading, friendly 404 for a path that
  doesn't exist, empty state before any citation is clicked.

### 5. Polish floor (not ceiling)
- Loading / empty / error states on every screen; no unhandled promise
  rejections; **zero console errors** in the happy path.
- Narrow viewports: stack the panes (viewer below or as a sheet).
  Degrade gracefully; do not gold-plate mobile.
- Design direction, briefly: calm dev-tool aesthetic — generous
  whitespace, monospace for paths/symbols/code, one restrained accent
  color, the step timeline visually the hero. Use shadcn defaults as
  the base and do not fight them for pixels (ROADMAP's "do not").
  Dark mode only if it falls out of the theme setup cheaply.

## Verification — live, in a browser, narrate it

With api + worker running, walk the ROADMAP done-when and report it as
a step-by-step narration (what you did, what appeared):

1. `pnpm build`, `pnpm lint`, `vitest` — all green (paste summaries).
2. Submit a **fresh** small public Python repo not yet in the DB
   (e.g. `https://github.com/pallets/blinker`) → watch the status page
   walk queued→cloning→parsing→linking→embedding→ready with moving
   progress bars.
3. Open the **httpx** repo's chat. Ask an EVAL-style question (e.g.
   "How does httpx decide which transport to use for a request?").
   Watch the step timeline stream, then the answer, then citations.
4. Click a citation → the viewer loads the file, scrolls, and
   highlights the exact range. Open the file in the repo to confirm
   the highlighted lines are the cited code.
5. Refresh mid-conversation (after an answer completes) → transcript
   restored from sessionStorage, status pages still correct.
6. Error paths: submit an invalid URL (inline 422); visit a bogus repo
   id (friendly 404 page); if you can provoke a failed ingest cheaply
   (bad-but-valid-looking GitHub URL), demonstrate the Retry
   re-enqueue — otherwise cover it with the backend test only and say
   so.
7. Browser console clean throughout the happy path.

## Wrap up

1. ROADMAP Phase 5 → done, boxes ticked with the narration as
   evidence.
2. DECISIONS: the useRepoChat reconciliation; the pre-authorized
   re-enqueue exception; the sessionStorage transcript note.
3. HANDOFF: Phase 5 outcome, how to run the full stack (three
   processes now), Phase 6 next.
4. Final commit. ≤10-line summary + one sentence on what the demo
   feels like to use — honestly, including anything that feels slow or
   janky (that is Phase 6 hardening input, not failure).
