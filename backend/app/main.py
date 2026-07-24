"""FastAPI entrypoint.

Phase 0: only ``GET /health``. The lifespan opens an asyncpg pool but tolerates
connection failure with a logged warning — ``/health`` must not require any
service to be up (CLAUDE.md hard rule 1: no work in handlers; here, no service
dependency for the liveness probe).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from app.config import get_settings
from app.db.pool import close_pool, create_pool

logger = logging.getLogger("app.main")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    app.state.pool = None
    try:
        app.state.pool = await create_pool(settings.DATABASE_URL)
        logger.info("asyncpg pool created")
    except Exception as exc:  # noqa: BLE001 — tolerate any connection failure
        logger.warning("could not create asyncpg pool at startup: %s", exc)
    try:
        yield
    finally:
        await close_pool(app.state.pool)


app = FastAPI(title="Codebase Onboarding Assistant", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, bool]:
    return {"ok": True}
