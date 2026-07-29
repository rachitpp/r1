"""FastAPI entrypoint (SPEC §8).

Owns process-level resources and nothing else: the asyncpg pool, the ARQ queue
handle, the embedder, the middleware stack, and the §8 exception mapping. Routes
live in ``app/api/routes.py``.

Startup is deliberately failure-tolerant for Postgres and Redis — ``/health``
must answer even when a dependency is down, so an unreachable service is a
logged warning here and a 503 from the endpoint that actually needs it, not a
process that refuses to boot. ``/ready`` is where that tolerance is made
visible: it is the endpoint that says whether this process can serve anything,
and it is what a load balancer should route on.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from arq.connections import RedisSettings
from arq.connections import create_pool as create_arq_pool
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware

from app.api.auth_routes import router as auth_router
from app.api.errors import register_error_handlers
from app.api.middleware import (
    REQUEST_ID_HEADER,
    BodySizeLimitMiddleware,
    RateLimitMiddleware,
    RequestContextMiddleware,
)
from app.api.routes import router
from app.config import get_settings
from app.db.pool import close_pool, create_pool
from app.ingest.embedder import get_embedder, shutdown_inference
from app.logging_setup import configure_logging

logger = logging.getLogger("app.main")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # uvicorn configures its own loggers and leaves the root logger alone, so
    # without this every `app.*` INFO line (including "enqueued ingest") is
    # silently dropped.
    configure_logging()
    settings = get_settings()
    app.state.pool = None
    app.state.arq = None
    app.state.embedder_ready = False
    try:
        app.state.pool = await create_pool(settings.DATABASE_URL)
        logger.info(
            "asyncpg pool created (min=%d max=%d command_timeout=%ss)",
            settings.DB_POOL_MIN_SIZE,
            settings.DB_POOL_MAX_SIZE,
            settings.DB_COMMAND_TIMEOUT_S,
        )
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
        app.state.embedder_ready = True
        logger.info("embedder warm: %s", settings.EMBEDDING_MODEL)
    except Exception as exc:  # noqa: BLE001 — a cold model is not a dead API
        logger.warning("could not warm the embedder at startup: %s", exc)

    try:
        yield
    finally:
        shutdown_inference()
        await close_pool(app.state.pool)
        if app.state.arq is not None:
            await app.state.arq.aclose()


app = FastAPI(title="Codebase Onboarding Assistant", lifespan=lifespan)
_settings = get_settings()

# Middleware, innermost first. `add_middleware` prepends, so the LAST call here
# is the OUTERMOST layer at runtime — read this block bottom-up. The intended
# order, outside in, and why (see app/api/middleware.py for the long version):
#
#   RequestContext  — must wrap everything, including the rejections below it
#   CORS            — must wrap the limiters, or a browser cannot read a 429
#   GZip            — skips text/event-stream on its own, so SSE is unaffected
#   BodySizeLimit   — before anything parses a body
#   RateLimit       — innermost of ours: still counted, still correlated
app.add_middleware(RateLimitMiddleware, settings=_settings)
app.add_middleware(BodySizeLimitMiddleware)
# 1 KB floor: below that, gzip's header costs more than it saves. The endpoint
# this exists for is `GET /repos/{id}/files`, where source code compresses ~4:1.
app.add_middleware(GZipMiddleware, minimum_size=1024)
# The frontend is a separate origin in dev (:3000 -> :8000). `expose_headers` is
# what lets the browser actually read the request id it needs in a bug report.
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.frontend_origins,
    allow_origin_regex=_settings.FRONTEND_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=[REQUEST_ID_HEADER],
)
app.add_middleware(
    RequestContextMiddleware, trust_proxy=_settings.TRUST_PROXY_HEADERS
)

register_error_handlers(app)
app.include_router(auth_router)
app.include_router(router)
