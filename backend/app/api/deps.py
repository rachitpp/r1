"""FastAPI dependencies: the pool, a connection, the queue, and the chat model.

The chat model is a dependency rather than a direct
:func:`app.agent.model.build_chat_model` call so tests can override it with the
Phase 3 scripted fake model and exercise the whole SSE pipeline without a
network or an API key.

Two connection dependencies, deliberately:

* :data:`Conn` — one pooled connection for the request. Right for the CRUD
  routes, which are a query or two and are done in milliseconds.
* :data:`Pool` — the pool itself, for the chat route. An agent run lasts as long
  as the model takes; a connection held for that span is one nobody else can
  have, and ``CHAT_MAX_CONCURRENCY`` of them would starve every other endpoint
  in the process. The agent borrows per tool call instead (``app/db/pool.py``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Annotated

import asyncpg
from arq.connections import ArqRedis
from fastapi import Depends, Request
from langchain_core.language_models.chat_models import BaseChatModel

from app.agent.model import build_chat_model
from app.db.pool import acquire
from app.exceptions import QueueUnavailableError


def get_pool(request: Request) -> asyncpg.Pool:
    """The process-wide asyncpg pool, created in the lifespan."""
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise QueueUnavailableError("database pool unavailable")
    return pool


async def get_conn(request: Request) -> AsyncIterator[asyncpg.Connection]:
    """One pooled connection for the duration of a request.

    Fine for every route except chat, which takes the pool instead. Goes through
    :func:`app.db.pool.acquire` so the time spent waiting for a connection is
    measured — the first number that moves when the pool is the bottleneck.
    """
    async with acquire(get_pool(request)) as conn:
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


@lru_cache(maxsize=1)
def _cached_chat_model() -> BaseChatModel:
    """Build the chat model once per process.

    Provider clients own an HTTP connection pool. Constructing one per request
    means a fresh TCP connection and TLS handshake to the provider for every
    question — paid on the critical path, before a single token is generated —
    and throws away keep-alive between them. The client is stateless across
    calls, so one instance serves every request.
    """
    return build_chat_model()


def get_chat_model() -> BaseChatModel:
    """The agent's chat model (provider chosen by ``AGENT_MODEL``)."""
    return _cached_chat_model()


# Annotated aliases so route signatures stay readable and no `Depends()` call
# sits in a default argument.
Conn = Annotated[asyncpg.Connection, Depends(get_conn)]
Pool = Annotated[asyncpg.Pool, Depends(get_pool)]
Arq = Annotated[ArqRedis, Depends(get_arq)]
ChatModel = Annotated[BaseChatModel, Depends(get_chat_model)]
