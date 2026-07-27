"""FastAPI dependencies: the pool, a connection, the queue, and the chat model.

The chat model is a dependency rather than a direct
:func:`app.agent.model.build_chat_model` call so tests can override it with the
Phase 3 scripted fake model and exercise the whole SSE pipeline without a
network or an API key.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

import asyncpg
from arq.connections import ArqRedis
from fastapi import Depends, Request
from langchain_core.language_models.chat_models import BaseChatModel

from app.agent.model import build_chat_model
from app.exceptions import QueueUnavailableError


def get_pool(request: Request) -> asyncpg.Pool:
    """The process-wide asyncpg pool, created in the lifespan."""
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise QueueUnavailableError("database pool unavailable")
    assert isinstance(pool, asyncpg.Pool)
    return pool


async def get_conn(request: Request) -> AsyncIterator[asyncpg.Connection]:
    """One pooled connection for the duration of a request.

    The chat endpoint is the reason this is a dependency rather than a
    ``pool.acquire()`` inside each handler: its connection has to stay checked
    out for the whole SSE stream, and FastAPI already owns that lifetime.
    """
    pool = get_pool(request)
    async with pool.acquire() as conn:
        yield conn


def get_arq(request: Request) -> ArqRedis:
    """The ARQ queue handle, created in the lifespan.

    Raises when Redis was unreachable at startup: ingestion never runs in the
    handler (hard rule 1), so there is no inline fallback to offer.
    """
    arq = getattr(request.app.state, "arq", None)
    if arq is None:
        raise QueueUnavailableError(
            "redis unavailable; the ingest queue cannot accept jobs"
        )
    assert isinstance(arq, ArqRedis)
    return arq


def get_chat_model() -> BaseChatModel:
    """The agent's chat model (provider chosen by ``AGENT_MODEL``)."""
    return build_chat_model()


# Annotated aliases so route signatures stay readable and no `Depends()` call
# sits in a default argument.
Conn = Annotated[asyncpg.Connection, Depends(get_conn)]
Arq = Annotated[ArqRedis, Depends(get_arq)]
ChatModel = Annotated[BaseChatModel, Depends(get_chat_model)]
