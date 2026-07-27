"""Typed exceptions -> HTTP responses (SPEC §8).

The only place in the codebase that knows about status codes. Services raise
domain errors (CLAUDE.md conventions); this maps them once, so no handler grows
its own ``try/except HTTPException`` and no two endpoints answer the same
condition differently.
"""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.exceptions import (
    InvalidRepoUrlError,
    QueueUnavailableError,
    RepoFileNotFoundError,
    RepoNotFoundError,
    RepoNotReadyError,
)


async def _not_found(_request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND, content={"detail": str(exc)}
    )


async def _invalid_url(_request: Request, exc: Exception) -> JSONResponse:
    # 422, matching FastAPI's own validation failures: the URL is the wrong
    # shape, which is a body problem, not a missing resource.
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={"detail": str(exc)},
    )


async def _not_ready(_request: Request, exc: Exception) -> JSONResponse:
    """409 with the current status in the body, exactly as §8 specifies."""
    assert isinstance(exc, RepoNotReadyError)
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={"detail": "repo not ready", "status": exc.status},
    )


async def _unavailable(_request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content={"detail": str(exc)}
    )


def register_error_handlers(app: FastAPI) -> None:
    """Install the §8 exception -> status-code mapping on ``app``."""
    app.add_exception_handler(RepoNotFoundError, _not_found)
    app.add_exception_handler(RepoFileNotFoundError, _not_found)
    app.add_exception_handler(InvalidRepoUrlError, _invalid_url)
    app.add_exception_handler(RepoNotReadyError, _not_ready)
    app.add_exception_handler(QueueUnavailableError, _unavailable)
