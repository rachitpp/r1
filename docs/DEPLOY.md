# DEPLOY.md — putting this online

The project runs locally today (see the README). This is the guide for standing
it up on the internet. It has been written to be followable, **not executed** —
no live URL exists yet, and nothing here is claimed to have been run end to end
in the cloud. Where something is inferred from local behaviour rather than
observed in a deployment, it says so.

Budget an afternoon. The one thing that makes this more than a one-click deploy
is the worker.

---

## The shape of it

Four pieces, three of which are ordinary:

```
  Vercel                Railway / Fly                    managed
┌──────────┐        ┌───────────────────┐        ┌──────────────────┐
│ Next.js  │ ──SSE──▶│ web:    uvicorn   │───────▶│ Neon (Postgres   │
│ frontend │        │                   │        │  + pgvector)     │
└──────────┘        │ worker: arq       │───────▶│ Redis Cloud /    │
                    └───────────────────┘        │  Upstash         │
                                                 └──────────────────┘
```

**The worker is a second long-running process, not a flag on the API.** It is
the single most likely thing to cost you a day. Ingestion never happens in an
HTTP handler (hard rule 1) — the API enqueues an ARQ job and returns, and if
nothing is consuming that queue, a submitted repo sits at 0% forever and
reports no error. It is not serverless-compatible: it holds an embedding model
in memory and runs 8-minute jobs.

## Prerequisites

- Accounts: Vercel, Railway (or Fly), Neon, Redis Cloud (or Upstash)
- A model API key — `console.mistral.ai` for the default `AGENT_MODEL`
- The repo pushed to GitHub

---

## 1. Postgres — Neon

1. Create a project; copy the pooled connection string.
2. Enable pgvector once, from the Neon SQL editor:
   ```sql
   CREATE EXTENSION IF NOT EXISTS vector;
   ```
   The migrations assume the extension exists. Without it `001_init.sql` fails
   on the `vector` column type.
3. Keep the `?sslmode=require` suffix Neon gives you — asyncpg needs it.

## 2. Redis — Redis Cloud or Upstash

Copy the DSN. Two things bite here:

- **Paste the bare DSN only.** A console's "connect" snippet often looks like
  `redis-cli -u redis://...`. That prefix is not part of the DSN and the worker
  fails at import with `RuntimeError: invalid DSN scheme`.
- **Free tiers meter commands, and ARQ polls.** ARQ's 0.5s default would spend
  ~172,800 commands/day/worker; Upstash's free tier is 500K/month, so the
  default would exhaust it in about three days. This repo already sets
  `poll_delay = 2.0s` in `app/worker.py` for exactly that reason — leave it
  alone unless you are on a paid plan and want faster pickup.

## 3. API + worker — Railway or Fly

One repo, one image, **two services**. Root directory `backend/`.

| | web service | worker service |
|---|---|---|
| start command | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` | `arq app.worker.WorkerSettings` |
| public URL | yes | **no** |
| min instances | 1 | 1 |
| memory | ≥1 GB | ≥2 GB |

Both need the same environment:

```
DATABASE_URL=postgresql://...neon.tech/neondb?sslmode=require
REDIS_URL=redis://default:...@...:6379
AGENT_MODEL=mistral-medium-latest
MISTRAL_API_KEY=...
FRONTEND_ORIGIN=https://<your-app>.vercel.app
```

Three deployment-specific notes:

- **Memory.** The embedding model (`bge-small-en-v1.5`) loads into both
  processes. Locally the API sits around 1 GB resident and the worker higher
  during ingest. The ≥2 GB figure for the worker is sized from local
  observation, not from a deployed run — start there and watch. If you set
  `RERANK_ENABLED=true` you also load a ~2.4 GB cross-encoder; don't, it
  measured worse than plain fusion (SPEC §5.3).
- **Cold start is slow and silent.** The API warms the embedder during startup,
  which takes ~30s locally on first run and longer when the model has to be
  downloaded. Set health-check grace//timeout above that or the platform will
  kill the container mid-warm and retry forever. The model downloads on every
  fresh container unless you bake it into the image or mount a persistent
  `HF_HOME`.
- **Clone disk.** Ingestion clones repos to a temp dir and deletes them when the
  job finishes. It needs writable scratch space, not a persistent volume.

### Migrations

Run once, after the database exists and before first traffic:

```bash
DATABASE_URL='<neon-url>' uv run python scripts/migrate.py
```

From your machine is fine — it is a plain script against the DSN. `scripts/`
tracks applied versions, so re-running is safe and does nothing.

## 4. Frontend — Vercel

- Root directory: `frontend/`
- Framework preset: Next.js (auto-detected)
- Environment: `NEXT_PUBLIC_API_URL=https://<your-api>.up.railway.app`

`NEXT_PUBLIC_*` is inlined at **build** time, not read at runtime — change it
and you must redeploy, not just restart.

## 5. CORS, which is where this usually fails first

`FRONTEND_ORIGIN` on the API must be the frontend's origin, **exactly**: scheme
+ host + port, no trailing slash. It is compared as a string, so
`https://app.vercel.app` and `https://app.vercel.app/` are different, and so are
`localhost` and `127.0.0.1`.

A mismatch does not look like a server error. The browser reports
`No 'Access-Control-Allow-Origin' header is present`, the request never reaches
your handler, and the API logs a `400` on `OPTIONS` — or nothing at all. If the
frontend loads but every fetch fails, check this before anything else.

Two variables:

- `FRONTEND_ORIGIN` — comma-separated list of exact origins. Set this in
  production to the Vercel URL. Add preview-deployment origins here too, since
  Vercel gives each one its own hostname.
- `FRONTEND_ORIGIN_REGEX` — pattern alternative, meant for local development
  where a forwarded port moves between sessions. **Leave it unset in
  production.**

## 6. Smoke test

```bash
curl https://<api>/health                      # {"ok": true}
curl https://<api>/repos                       # [] on a fresh database
```

Then in the browser: submit a small repo (`pallets-eco/blinker` ingests in well
under a minute), watch the progress bar move past 0% — that is your proof the
worker is alive and connected to the same Redis — and ask a question. Streaming
tokens with citations means the whole chain works.

If progress never leaves 0%, the worker is the problem in almost every case:
not deployed, crashed on boot, or pointed at a different `REDIS_URL` than the
API.

---

## What this costs

Every managed piece has a free tier that fits a portfolio demo: Neon, Redis
Cloud/Upstash, Vercel hobby, and Mistral's token-metered free tier. Railway and
Fly are the ones that will ask for money, because two always-on processes with
1–2 GB of memory are not a free-tier shape anywhere. Expect a few dollars a
month, and note that scale-to-zero is not an option for the worker.

## Known gaps

Stated plainly, because a deploy guide that hides them wastes your afternoon:

- **Never executed.** Every step here is derived from the working local system
  and the platforms' documented behaviour. Expect one or two surprises.
- **No Dockerfile in the repo.** Railway and Fly can build the backend from
  `pyproject.toml`/`uv.lock` via buildpacks, but if you want reproducibility,
  writing one is the first thing to do.
- **No auth.** v1 is single-user by design (`CLAUDE.md`). A public URL means
  anyone can queue ingest jobs against your model key. Put the deployment
  behind platform-level access control, or accept it knowingly for a demo.
- **No repo-size guard at submit.** A large repo will occupy the single worker
  for a long time; `max_jobs = 1` means everything else waits.
