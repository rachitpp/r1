"""Per-IP rate limiting and concurrency gating.

Two different "no" answers, both 429, for two different reasons:

* **Rate limit** — this caller has had their share of a window. Enforced in
  Redis (:class:`RedisRateLimiter`) so the count is shared across every API
  process, which an in-memory counter would not be.
* **Concurrency** — nobody is at fault, the process is simply full
  (:class:`Slots`). Per-process by definition: it is guarding *this* box's
  cores and connections.

`slowapi` is the obvious dependency for the first one, and CLAUDE.md rule 11
says ask before adding one. Redis is already here for the queue, and a fixed
window is thirty lines, so it is written out.

**Fixed window, not sliding.** A fixed window allows a burst of up to 2×limit
across a boundary. For limits whose job is "an anonymous caller cannot queue a
hundred repo clones", that is irrelevant, and it costs one Redis command per
request instead of the sorted-set read-modify-write a sliding window needs. On a
managed free tier the command budget is a real constraint (see
``app/worker.py``), so the cheap algorithm is the right one.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Protocol

from app.config import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Rule:
    """One limit: ``limit`` requests per ``window_s`` seconds, per identity."""

    name: str
    method: str  # an HTTP method, or "*" for any
    pattern: re.Pattern[str]
    limit: int
    window_s: int

    def matches(self, method: str, path: str) -> bool:
        if self.method != "*" and self.method != method:
            return False
        return self.pattern.fullmatch(path) is not None


# Polled by infrastructure, not by users. Rate-limiting your own health check is
# how a load balancer decides a healthy process is down.
EXEMPT_PATHS = frozenset({"/health", "/ready", "/metrics"})

_REPOS = re.compile(r"/repos/?")
_CHAT = re.compile(r"/repos/[^/]+/chat/?")
_ANY = re.compile(r".*")


def rules_for(settings: Settings) -> tuple[Rule, ...]:
    """The rule table, most specific first — first match wins.

    The two expensive endpoints are named explicitly and the catch-all is
    generous: reads are cheap, and a limit tight enough to matter for them would
    only break the frontend's ingest-progress polling.
    """
    return (
        Rule(
            "ingest",
            "POST",
            _REPOS,
            settings.RATE_LIMIT_INGEST_PER_HOUR,
            3_600,
        ),
        Rule("chat", "POST", _CHAT, settings.RATE_LIMIT_CHAT_PER_HOUR, 3_600),
        Rule("default", "*", _ANY, settings.RATE_LIMIT_DEFAULT_PER_MINUTE, 60),
    )


def match_rule(rules: tuple[Rule, ...], method: str, path: str) -> Rule | None:
    """First rule matching ``method``/``path``, or ``None`` if the path is exempt."""
    if path in EXEMPT_PATHS:
        return None
    for rule in rules:
        if rule.matches(method, path):
            return rule
    return None


class RedisLike(Protocol):
    """The two commands the limiter needs. Narrow on purpose: it makes the
    limiter testable without Redis, and documents the command cost per request."""

    async def incr(self, name: str) -> int: ...

    async def expire(self, name: str, time: int) -> bool: ...


class RedisRateLimiter:
    """Fixed-window counter in Redis. Fails **open**.

    An unreachable Redis already costs this application its ingest queue (503
    from ``POST /repos``). Turning it into a total outage — refusing reads
    because the thing that counts reads is down — trades a partial failure for a
    complete one. The exception is logged and the request proceeds.
    """

    def __init__(self, redis: RedisLike | None) -> None:
        self._redis = redis

    async def check(self, rule: Rule, identity: str) -> int | None:
        """Count one request. Returns ``None`` to allow, or seconds to retry after.

        The window index is baked into the key, so an expired window is a
        different key rather than a value that has to be reset.
        """
        if self._redis is None:
            return None
        now = int(time.time())
        window_start = now - (now % rule.window_s)
        key = f"rl:{rule.name}:{identity}:{window_start}"
        try:
            count = await self._redis.incr(key)
            if count == 1:
                # Only on the first request of a window: one command per request
                # in the steady state. If this call is lost the key outlives its
                # window, which leaks one small key and blocks nothing — the next
                # window has a different key.
                await self._redis.expire(key, rule.window_s)
        except Exception as exc:  # noqa: BLE001 — see the fail-open note above
            logger.warning("rate limiter unavailable, allowing request: %s", exc)
            return None
        if count > rule.limit:
            return max(1, window_start + rule.window_s - now)
        return None


class Slots:
    """A counting gate that never waits: you get a slot, or you are told no.

    A :class:`asyncio.Semaphore` would queue instead, which is the wrong answer
    for an SSE endpoint — a caller parked behind eight agent runs is holding a
    socket open for minutes to eventually be served badly. Refusing immediately
    with a ``Retry-After`` is both faster and more honest.

    No lock: every caller is on the one event loop, and neither method awaits, so
    there is no point at which two of them interleave.
    """

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._used = 0

    @property
    def used(self) -> int:
        return self._used

    def try_acquire(self) -> bool:
        if self._used >= self.limit:
            return False
        self._used += 1
        return True

    def release(self) -> None:
        # Clamped rather than asserted: a double release is a bug worth logging,
        # never a reason to take a live server down.
        if self._used > 0:
            self._used -= 1
        else:  # pragma: no cover — defensive
            logger.warning("Slots.release() called with nothing acquired")
