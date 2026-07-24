"""ARQ worker entrypoint.

Phase 0: connects to Redis and exposes one no-op ``ping`` task. Real ingestion
tasks arrive in Phase 4. Redis settings come from config (CLAUDE.md rule 12).
"""

from __future__ import annotations

import logging
from typing import Any

from arq.connections import RedisSettings

from app.config import get_settings

logger = logging.getLogger("app.worker")


async def ping(ctx: dict[str, Any]) -> str:
    """No-op health task."""
    return "pong"


async def on_startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    logger.info(
        "arq worker up | redis=%s | embedding_model=%s | agent_model=%s",
        settings.REDIS_URL,
        settings.EMBEDDING_MODEL,
        settings.AGENT_MODEL or "<unset>",
    )


class WorkerSettings:
    """ARQ worker configuration. ``arq`` reads these class attributes."""

    functions = [ping]
    on_startup = on_startup
    redis_settings = RedisSettings.from_dsn(get_settings().REDIS_URL)
