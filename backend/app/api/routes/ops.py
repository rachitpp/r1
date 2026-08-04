"""Operational endpoints: ``/health`` (liveness), ``/ready`` (can this
process actually serve anything), and ``/metrics``. Not part of the §8
API surface — they answer about the process, not about a repo."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status

from app import metrics
from app.api.routes._common import chat_slots
from app.api.schemas import (
    ReadyCheck,
    ReadyOut,
)
from app.config import (
    get_settings,
)
from app.db.pool import acquire, sample_pool_gauges
from app.exceptions import (
    UnauthorizedError,
)

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, bool]:
    """Liveness. Deliberately trivial: is this process running at all.

    It must not touch Postgres or Redis. A liveness probe that fails when a
    dependency is down gets the process *restarted* for someone else's outage,
    which turns a degraded API into no API. "Can it serve?" is ``/ready``.
    """
    return {"ok": True}


@router.get("/ready", response_model=ReadyOut)
async def ready(request: Request, response: Response) -> ReadyOut:
    """Readiness: can this process serve a real request right now.

    Startup tolerates an unreachable Postgres or Redis on purpose (see
    :mod:`app.main`), which is what makes this endpoint necessary rather than
    decorative: without it, a process that will 503 every single request still
    reports itself healthy, and a load balancer routes traffic straight into it.

    Postgres is required. Redis is required too — without it ``POST /repos``
    cannot enqueue, and a node that can only answer reads is not ready. The
    embedder is reported but *not* required: it loads lazily on first use, so a
    cold model is a slow first search, not a broken node.
    """
    checks: dict[str, ReadyCheck] = {}

    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        checks["postgres"] = ReadyCheck(ok=False, detail="no pool")
    else:
        try:
            async with acquire(pool) as conn:
                await conn.fetchval("SELECT 1")
            checks["postgres"] = ReadyCheck(ok=True)
        except Exception as exc:  # noqa: BLE001 — the check *is* the error path
            checks["postgres"] = ReadyCheck(ok=False, detail=type(exc).__name__)

    arq = getattr(request.app.state, "arq", None)
    if arq is None:
        checks["redis"] = ReadyCheck(ok=False, detail="not connected")
    else:
        try:
            await arq.ping()
            checks["redis"] = ReadyCheck(ok=True)
        except Exception as exc:  # noqa: BLE001
            checks["redis"] = ReadyCheck(ok=False, detail=type(exc).__name__)

    warm = bool(getattr(request.app.state, "embedder_ready", False))
    checks["embedder"] = ReadyCheck(
        ok=True, detail="warm" if warm else "cold (loads on first use)"
    )

    ok = checks["postgres"].ok and checks["redis"].ok
    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadyOut(ok=ok, checks=checks)


@router.get("/metrics")
async def metrics_endpoint(request: Request) -> Response:
    """Prometheus text exposition (see :mod:`app.metrics`).

    Guarded by ``METRICS_TOKEN`` when one is set. Metrics are not secret in the
    way credentials are, but they do enumerate repo counts, error rates, and
    what this box is doing — restrict the network or set the token.
    """
    settings = get_settings()
    if not settings.METRICS_ENABLED:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    if settings.METRICS_TOKEN:
        header = request.headers.get("authorization", "")
        if header != f"Bearer {settings.METRICS_TOKEN}":
            raise UnauthorizedError("metrics require a bearer token")

    sample_pool_gauges(getattr(request.app.state, "pool", None))
    metrics.chat_streams_active.set(chat_slots.used)
    return Response(
        content=metrics.render(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


# ---------------------------------------------------------------------------
# §8 API
# ---------------------------------------------------------------------------
