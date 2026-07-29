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
from typing import TYPE_CHECKING, Annotated, Any

import asyncpg
from arq.connections import ArqRedis
from fastapi import Depends, Request

from app.agent.model import build_chat_model

if TYPE_CHECKING:
    # Typing only: importing this at runtime pulls `transformers` and therefore
    # torch — 185 MB measured, more than the embedding model (SPEC §16.1).
    from langchain_core.language_models.chat_models import BaseChatModel
from app.auth import tokens
from app.config import SESSION_COOKIE, get_settings
from app.db import queries
from app.db.pool import acquire
from app.exceptions import QueueUnavailableError, UnauthorizedError


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
# `Any`, not `BaseChatModel`, and this is the one place it costs something.
# FastAPI evaluates this alias at import time, so a real class here would drag
# torch into every API replica; a string forward reference does not work either,
# because FastAPI resolves it with `get_type_hints` against module globals where
# a TYPE_CHECKING import is absent. The dependency is still fully typed — see
# `get_chat_model` above — so what is lost is the annotation on route
# parameters, not the return type anything reasons about.
ChatModel = Annotated[Any, Depends(get_chat_model)]


# ---------------------------------------------------------------------------
# Identity (SPEC §13)
# ---------------------------------------------------------------------------


def session_token(request: Request) -> str | None:
    """The caller's session token, cookie first, then bearer header (§13.4).

    Cookie first because that is what a browser sends and what HttpOnly
    protects; the header exists for the CLIs and the tests, which have no
    cookie jar. Neither is trusted here — this only locates the string.
    """
    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie:
        return cookie
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() == "bearer" and value:
        return value
    return None


async def get_current_user(request: Request, conn: Conn) -> asyncpg.Record:
    """Resolve the signed-in user, or 401 (§13.5).

    The user row is re-read on every request rather than trusted from the
    token: a token is a claim about *who*, not a snapshot of the account, and a
    deleted user must stop working immediately rather than at expiry.
    """
    settings = get_settings()
    token = session_token(request)
    if not token or not settings.SESSION_SECRET:
        raise UnauthorizedError("sign-in required")
    user_id = tokens.verify(token, settings.SESSION_SECRET)
    if user_id is None:
        raise UnauthorizedError("session is invalid or has expired")
    row = await queries.get_user(conn, user_id)
    if row is None:
        # Signed correctly, but the account is gone. Same message as an invalid
        # token: the difference is the server's business, not a probe's.
        raise UnauthorizedError("session is invalid or has expired")
    return row


CurrentUser = Annotated[asyncpg.Record, Depends(get_current_user)]
