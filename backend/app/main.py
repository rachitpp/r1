"""FastAPI entrypoint (SPEC §8).

Owns process-level resources and nothing else: the asyncpg pool, the ARQ queue
handle, the embedder, CORS, and the §8 exception mapping. Routes live in
``app/api/routes.py``.

Startup is deliberately failure-tolerant for Postgres and Redis — ``/health``
must answer even when a dependency is down, so an unreachable service is a
logged warning here and a 503 from the endpoint that actually needs it, not a
process that refuses to boot.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from arq.connections import RedisSettings
from arq.connections import create_pool as create_arq_pool
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import register_error_handlers
from app.api.routes import router
from app.config import get_settings
from app.db.pool import close_pool, create_pool
from app.ingest.embedder import get_embedder

logger = logging.getLogger("app.main")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # uvicorn configures its own loggers and leaves the root logger alone, so
    # without this every `app.*` INFO line (including "enqueued ingest") is
    # silently dropped.
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    settings = get_settings()
    app.state.pool = None
    app.state.arq = None
    try:
        app.state.pool = await create_pool(settings.DATABASE_URL)
        logger.info("asyncpg pool created")
    except Exception as exc:  # noqa: BLE001 — tolerate any connection failure
        logger.warning("could not create asyncpg pool at startup: %s", exc)
    try:
        app.state.arq = await create_arq_pool(
            RedisSettings.from_dsn(settings.REDIS_URL)
        )
        logger.info("arq queue connected")
    except Exception as exc:  # noqa: BLE001 — /health must not need Redis
        logger.warning("could not connect to redis at startup: %s", exc)

    # Warm the embedder here rather than on first use (SPEC §4, Phase 4
    # Reconciliation 3): every chat request runs `search_code`, and Phase 2's
    # lazy load made the first question of a session pay ~10s of model load
    # inside its own SSE stream. The 8 GB-host workaround that motivated the
    # lazy path is gone; lazy loading stays available for CLI and test paths.
    try:
        get_embedder()
        logger.info("embedder warm: %s", settings.EMBEDDING_MODEL)
    except Exception as exc:  # noqa: BLE001 — a cold model is not a dead API
        logger.warning("could not warm the embedder at startup: %s", exc)

    try:
        yield
    finally:
        await close_pool(app.state.pool)
        if app.state.arq is not None:
            await app.state.arq.aclose()


app = FastAPI(title="Codebase Onboarding Assistant", lifespan=lifespan)

# The frontend is a separate origin in dev (:3000 -> :8000). Configured now so
# Phase 5 does not open with a preflight failure.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[get_settings().FRONTEND_ORIGIN],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_error_handlers(app)
app.include_router(router)
