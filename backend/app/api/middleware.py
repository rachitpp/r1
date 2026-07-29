"""Cross-cutting request handling: correlation, size limits, rate limits.

All three are **pure ASGI** middleware rather than Starlette's
``BaseHTTPMiddleware``. That base class adapts a response into a
request/response function pair by pumping the body through an anyio memory
stream, which is fine for JSON and hostile to SSE — the one endpoint here that
must not be buffered. Pure ASGI passes ``send`` straight through, so a
``text/event-stream`` reaches the client exactly as it was yielded.

Order matters, and is set in :mod:`app.main`. Outermost to innermost:

1. :class:`RequestContextMiddleware` — so *everything*, including responses
   produced by the middleware below it, is timed, counted, and tagged.
2. ``CORSMiddleware`` — so a 429 or a 413 still carries the headers a browser
   needs in order to read it. Below CORS, a rejection reaches the frontend as an
   opaque network error and the user is told nothing.
3. ``GZipMiddleware``
4. :class:`BodySizeLimitMiddleware` — before anything parses the body.
5. :class:`RateLimitMiddleware` — last, so a limited request has still been
   counted and correlated.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from http.cookies import CookieError, SimpleCookie
from typing import Any

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app import metrics
from app.api.errors import error_response
from app.api.ratelimit import RedisRateLimiter, Rule, match_rule, rules_for
from app.auth import tokens
from app.config import (
    MAX_REQUEST_BYTES,
    SESSION_COOKIE,
    Settings,
    get_settings,
)
from app.exceptions import AppError, PayloadTooLargeError, TooManyRequestsError
from app.logging_setup import set_request_id

logger = logging.getLogger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"

# What an inbound request id may contain. Anything else is discarded and a fresh
# id is minted: this string is interpolated into every log line for the request,
# and an unvalidated header there is log injection.
_SAFE_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")

# Metric label for a path that matched no route. Without this, 404 scans would
# mint one time series per URL an attacker invents.
UNMATCHED = "__unmatched__"


def route_template(scope: Scope) -> str:
    """The matched route's pattern (``/repos/{repo_id}``), not the raw path.

    Labelling metrics by raw path is the standard way to take a Prometheus
    server down: every repo id would become its own time series, forever.
    """
    route = scope.get("route")
    path = getattr(route, "path", None)
    return str(path) if path else UNMATCHED


def client_identity(scope: Scope, *, trust_proxy: bool) -> str:
    """Who to rate-limit: the signed-in user if there is one, else the IP.

    **User first (SPEC §13.6).** An IP is a poor identity for a quota — a
    corporate NAT or a mobile carrier puts hundreds of unrelated people in one
    bucket, so one heavy user throttles a building. A verified user id is the
    thing the limit is actually about. `/auth/*` has no user yet and stays
    per-IP, which is also what keeps sign-in itself from being a free-for-all.

    The token is verified, not merely parsed, before it is trusted as an
    identity — an unverified subject would let anyone mint themselves a fresh
    quota by editing a cookie. Falling back to the IP on a bad token is
    deliberate: this middleware does not authenticate, it only counts, and
    rejecting the request is `get_current_user`'s job a few frames later.

    ``X-Forwarded-For`` is only believed when ``TRUST_PROXY_HEADERS`` says a
    proxy we control is the only way in. Otherwise it is a client-supplied
    string, and honouring it would let anyone reset their own limit by changing
    a header — a rate limiter that trusts the rate-limited is not one.
    """
    user_id = _identity_from_session(scope)
    if user_id is not None:
        return f"user:{user_id}"
    if trust_proxy:
        forwarded = Headers(scope=scope).get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    client = scope.get("client")
    return str(client[0]) if client else "unknown"


def _identity_from_session(scope: Scope) -> str | None:
    """The verified user id behind this request's session token, if any.

    Reads the raw ASGI scope rather than a `Request` because this runs as pure
    middleware, below FastAPI's dependency machinery.
    """
    secret = get_settings().SESSION_SECRET
    if not secret:
        return None
    headers = Headers(scope=scope)

    token: str | None = None
    cookie_header = headers.get("cookie")
    if cookie_header:
        jar = SimpleCookie()
        try:
            jar.load(cookie_header)
        except CookieError:
            return None
        morsel = jar.get(SESSION_COOKIE)
        if morsel:
            token = morsel.value
    if token is None:
        scheme, _, value = headers.get("authorization", "").partition(" ")
        if scheme.lower() == "bearer" and value:
            token = value
    if token is None:
        return None

    user_id = tokens.verify(token, secret)
    return str(user_id) if user_id else None


class RequestContextMiddleware:
    """Assign a request id, echo it, record what the request cost, and own the
    outcome — including the ones nothing else expected.

    The id goes into a context variable (see :mod:`app.logging_setup`), so every
    log line emitted anywhere beneath this — including a ``logger.exception``
    inside the agent loop — carries it without a single function having to take
    a ``request_id`` parameter.

    The catch-all for unmapped exceptions lives here rather than as an
    ``Exception`` handler on the app, because Starlette re-raises after running
    one of those: the client would get our envelope, and then the metric and the
    access log would record something else.
    """

    def __init__(self, app: ASGIApp, *, trust_proxy: bool = False) -> None:
        self.app = app
        self.trust_proxy = trust_proxy

    def _incoming_id(self, scope: Scope) -> str | None:
        """A caller-supplied id, if we are configured to believe callers."""
        if not self.trust_proxy:
            return None
        value = Headers(scope=scope).get(REQUEST_ID_HEADER.lower())
        if value and _SAFE_REQUEST_ID.fullmatch(value):
            return value
        return None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = self._incoming_id(scope) or uuid.uuid4().hex[:16]
        set_request_id(request_id)

        status = 500
        started = time.perf_counter()
        response_started = False

        async def send_with_id(message: Message) -> None:
            nonlocal status, response_started
            if message["type"] == "http.response.start":
                status = int(message["status"])
                response_started = True
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        metrics.http_requests_in_flight.inc()
        try:
            await self.app(scope, receive, send_with_id)
        except Exception as exc:
            # The catch-all for anything app/api/errors.py does not map. Handled
            # here rather than as an `Exception` handler on the app because
            # Starlette re-raises after running one of those, which would leave
            # the status metric and the access log disagreeing with what the
            # client actually received.
            #
            # `Exception`, not `BaseException`: a CancelledError is a client
            # hanging up, which is neither an error nor ours to answer.
            logger.exception("unhandled error serving %s", scope.get("path"))
            if response_started:
                # Headers are already on the wire; there is no status left to
                # change. Let it propagate so the server tears the connection
                # down rather than pretending the response completed.
                raise
            # Sent through `send_with_id`, so the 500 carries the request id the
            # log line above was written under.
            await error_response(exc)(scope, receive, send_with_id)
        finally:
            metrics.http_requests_in_flight.inc(-1)
            method = str(scope.get("method", "?"))
            # Read after the call: the router writes `route` into the scope while
            # handling, so the template is only knowable now.
            path = route_template(scope)
            metrics.http_request_duration.observe(
                time.perf_counter() - started, method=method, path=path
            )
            metrics.http_requests.inc(method=method, path=path, status=str(status))


class BodySizeLimitMiddleware:
    """Refuse request bodies over ``MAX_REQUEST_BYTES`` with a 413.

    Checked twice, because one check is not enough:

    * ``Content-Length`` up front, which every real client sends and which lets
      us refuse before a single byte of body is read.
    * A running total while reading, for chunked requests that declare no length
      at all — otherwise the header check is bypassed by omitting the header.

    The second check **buffers** the body here and replays it to the application
    rather than counting through a passthrough wrapper. The wrapper is the
    tidier design and it does not work: FastAPI catches everything
    ``request.body()`` raises and rewrites it into a generic 400, so an
    exception thrown from inside ``receive`` never reaches our handlers. Reading
    it here is the only place a 413 can still be sent. Buffering is affordable
    precisely because of the limit being enforced — nothing over 64 KB is ever
    held.
    """

    # Methods that carry no body. Skipped so a GET never waits on `receive`.
    _BODILESS = frozenset({"GET", "HEAD", "OPTIONS", "DELETE"})

    def __init__(self, app: ASGIApp, *, limit: int = MAX_REQUEST_BYTES) -> None:
        self.app = app
        self.limit = limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") in self._BODILESS:
            await self.app(scope, receive, send)
            return

        declared = Headers(scope=scope).get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.limit:
            await _error_response(
                scope, receive, send, PayloadTooLargeError(int(declared), self.limit)
            )
            return

        body = bytearray()
        trailing: Message | None = None
        more = True
        while more:
            message = await receive()
            if message["type"] != "http.request":
                trailing = message  # a disconnect; hand it straight on
                break
            body += message.get("body", b"")
            if len(body) > self.limit:
                await _error_response(
                    scope, receive, send, PayloadTooLargeError(len(body), self.limit)
                )
                return
            more = bool(message.get("more_body", False))

        replayed = False

        async def replay() -> Message:
            """The buffered body once, then the real channel.

            Delegating afterwards matters for SSE: sse-starlette keeps calling
            ``receive`` to notice the client hanging up, and a wrapper that only
            ever returned the body would leave it waiting forever.
            """
            nonlocal replayed
            if not replayed:
                replayed = True
                if trailing is not None:
                    return trailing
                return {
                    "type": "http.request",
                    "body": bytes(body),
                    "more_body": False,
                }
            return await receive()

        await self.app(scope, replay, send)


class RateLimitMiddleware:
    """Per-IP fixed-window limits, counted in Redis (:mod:`app.api.ratelimit`)."""

    def __init__(self, app: ASGIApp, *, settings: Settings) -> None:
        self.app = app
        self.enabled = settings.RATE_LIMIT_ENABLED
        self.trust_proxy = settings.TRUST_PROXY_HEADERS
        self.rules: tuple[Rule, ...] = rules_for(settings)

    def _redis(self, scope: Scope) -> Any:
        """The Redis handle, or ``None`` when the queue never connected.

        Reuses ARQ's connection rather than opening a second pool: it is the
        same server, and this is one command per request. A ``None`` here means
        the limiter fails open, which it is written to do.
        """
        app = scope.get("app")
        return getattr(getattr(app, "state", None), "arq", None)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self.enabled:
            await self.app(scope, receive, send)
            return

        rule = match_rule(self.rules, str(scope.get("method", "")), scope["path"])
        if rule is None:
            await self.app(scope, receive, send)
            return

        limiter = RedisRateLimiter(self._redis(scope))
        retry_after = await limiter.check(
            rule, client_identity(scope, trust_proxy=self.trust_proxy)
        )
        if retry_after is None:
            await self.app(scope, receive, send)
            return

        metrics.rate_limit_rejections.inc(rule=rule.name)
        await _error_response(
            scope,
            receive,
            send,
            TooManyRequestsError(
                f"rate limit exceeded for {rule.name} "
                f"({rule.limit} requests per {rule.window_s}s)",
                retry_after=retry_after,
                rule=rule.name,
            ),
        )


async def _error_response(
    scope: Scope, receive: Receive, send: Send, exc: AppError
) -> None:
    """Send a terminal error from middleware, in the handlers' own shape.

    Middleware sits outside FastAPI's exception handlers, so a rejection here
    cannot go through them. It goes through the same *builder* instead
    (:func:`app.api.errors.error_response`), which is the only way to be sure a
    413 from the size limiter and a 413 from a route look identical to the
    frontend.
    """
    await error_response(exc)(scope, receive, send)
