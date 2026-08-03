"""Typed exceptions -> HTTP responses (SPEC §8).

The only place in the codebase that knows about status codes. Services raise
domain errors (CLAUDE.md conventions); this maps them once, so no handler grows
its own ``try/except HTTPException`` and no two endpoints answer the same
condition differently.

Two things every response here has:

* **``request_id``**, so a user reporting "it said 500" hands over the one string
  that finds the server-side log line for their exact request.
* **A safe ``detail``.** Errors we authored are shown as written; anything else
  is redacted (:mod:`app.redact`), because an asyncpg connection error carries
  the DSN and a provider client can echo the credentials it just sent. An
  unrecognised exception gets no detail at all — only its request id.

:func:`error_response` is the single builder, used both by the handlers below
and by the middleware above them (which sits outside FastAPI's handler
machinery and would otherwise invent its own envelope).
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.exceptions import (
    AppError,
    AuthNotConfiguredError,
    ConversationNotFoundError,
    InvalidLineRangeError,
    InvalidRepoUrlError,
    OAuthError,
    PayloadTooLargeError,
    QueueUnavailableError,
    RepoFileNotFoundError,
    RepoNotFoundError,
    RepoNotReadyError,
    SharedAnswerNotFoundError,
    SymbolNotFoundError,
    TooManyRequestsError,
    UnauthorizedError,
)
from app.logging_setup import get_request_id
from app.redact import safe_error_text

logger = logging.getLogger(__name__)

# Exception type -> status code. Checked most-derived first by walking the MRO,
# so ServiceBusyError lands on TooManyRequestsError's 429 without repeating it.
_STATUS: dict[type[Exception], int] = {
    RepoNotFoundError: status.HTTP_404_NOT_FOUND,
    SharedAnswerNotFoundError: status.HTTP_404_NOT_FOUND,
    ConversationNotFoundError: status.HTTP_404_NOT_FOUND,
    SymbolNotFoundError: status.HTTP_404_NOT_FOUND,
    RepoFileNotFoundError: status.HTTP_404_NOT_FOUND,
    # 422, matching FastAPI's own validation failures: the URL is the wrong
    # shape, which is a body problem, not a missing resource.
    InvalidRepoUrlError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    InvalidLineRangeError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    UnauthorizedError: status.HTTP_401_UNAUTHORIZED,
    OAuthError: status.HTTP_400_BAD_REQUEST,
    AuthNotConfiguredError: status.HTTP_503_SERVICE_UNAVAILABLE,
    RepoNotReadyError: status.HTTP_409_CONFLICT,
    QueueUnavailableError: status.HTTP_503_SERVICE_UNAVAILABLE,
    TooManyRequestsError: status.HTTP_429_TOO_MANY_REQUESTS,
    PayloadTooLargeError: status.HTTP_413_CONTENT_TOO_LARGE,
}

INTERNAL_DETAIL = "internal server error"


def status_for(exc: BaseException) -> int:
    """Status code for ``exc``; 500 for anything unmapped."""
    for klass in type(exc).__mro__:
        if klass in _STATUS:
            return _STATUS[klass]
    return status.HTTP_500_INTERNAL_SERVER_ERROR


def error_response(exc: BaseException) -> JSONResponse:
    """The one error envelope: ``{detail, request_id}``, plus per-type extras."""
    code = status_for(exc)
    body: dict[str, object] = {
        "detail": safe_error_text(exc) if isinstance(exc, AppError) else INTERNAL_DETAIL,
        "request_id": get_request_id(),
    }
    headers: dict[str, str] = {}

    if isinstance(exc, RepoNotReadyError):
        # §8 specifies the current status in the body: the frontend uses it to
        # tell "come back in a minute" apart from "this one failed".
        body["detail"] = "repo not ready"
        body["status"] = exc.status
    if isinstance(exc, TooManyRequestsError):
        # A 429 without Retry-After invites the hammering it exists to stop.
        headers["Retry-After"] = str(exc.retry_after)

    return JSONResponse(status_code=code, content=body, headers=headers)


async def _handle(_request: Request, exc: Exception) -> JSONResponse:
    """Handler for every mapped exception type."""
    return error_response(exc)


def register_error_handlers(app: FastAPI) -> None:
    """Install the §8 exception -> status-code mapping on ``app``.

    Unmapped exceptions are *not* registered here. They are caught by
    :class:`app.api.middleware.RequestContextMiddleware`, which is outside this
    handler machinery and can therefore log the failure against the request id
    and still return the same envelope — where a handler registered for
    ``Exception`` would be re-raised by Starlette afterwards.
    """
    for klass in _STATUS:
        app.add_exception_handler(klass, _handle)
