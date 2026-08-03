"""Retry a provider rate limit instead of discarding the run (SPEC §7.2).

**The gap this closes.** `model.py` sets `max_retries=5` on every client and a
comment there claimed that covered 429s, "routine rather than exceptional" on a
free tier. It did not. `langchain_mistralai._create_retry_decorator` retries::

    errors = [httpx.RequestError, httpx.StreamError]

and a 429 raises `httpx.HTTPStatusError`, which is a **sibling** of
`RequestError` under `HTTPError`, not a subclass. So the configured retries
covered connection failures and never once covered the single most common
free-tier failure.

**Why it matters more than it looks.** The agent makes one model call per tool
round. A 429 on the eighth call throws away the seven tool calls already spent —
the run is not merely slow, it is wasted, and the user gets an error after
watching a tool timeline complete. Observed exactly that on a live run: seven
tool calls, then the whole answer discarded.

**Scope.** Only 429, and only the model call. A 400 is a bad request and will be
bad again; a 401 is a wrong key. Retrying those would turn a clear error into a
slow one. `Retry-After` is honoured when the provider sends it, because a
provider's own answer to "when should I come back" beats any backoff curve
invented here.
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Attempts, not retries: 1 is "no retry". Kept small because a rate limit that
# survives three waits is a quota problem, and making a user watch a fourth is
# just a slower way to tell them the same thing.
MAX_ATTEMPTS = 2
# Fallback backoff when the provider says nothing at all. Jittered so
# concurrent streams do not all come back at the same instant and re-trigger
# the limit they were waiting out.
BASE_DELAY_S = 2.0
MAX_DELAY_S = 60.0

# How long a per-minute token bucket takes to refill. Measured, not assumed:
# Mistral sends no `Retry-After`, but it does send
# `x-ratelimit-remaining-tokens-minute: 0` against a
# `x-ratelimit-limit-tokens-minute: 25000` budget. The first version of this
# module waited ~2s and then ~4s against that, which was never going to clear a
# minute-long window — it burned its attempts and reported the same failure
# more slowly.
MINUTE_WINDOW_S = 60.0


def _status_of(exc: BaseException) -> int | None:
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return int(status) if isinstance(status, int) else None


def is_rate_limit(exc: BaseException) -> bool:
    """Whether ``exc`` is a provider 429, whatever wrapper it arrives in."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429
    return _status_of(exc) == 429


def retry_after_seconds(exc: BaseException, attempt: int) -> float:
    """How long to wait: the provider's own answer, else jittered backoff."""
    response = getattr(exc, "response", None)
    header = getattr(response, "headers", {}) or {}
    raw = header.get("retry-after") if hasattr(header, "get") else None
    if raw:
        try:
            # Seconds form only. The HTTP-date form is legal and no provider
            # measured here uses it; guessing at a date parse would be more
            # code than the case deserves.
            return float(min(float(raw), MAX_DELAY_S))
        except (TypeError, ValueError):
            pass
    # No `Retry-After`, but a per-minute bucket the provider says is empty:
    # the only wait that helps is one long enough for the window to roll.
    if _minute_bucket_empty(header):
        return float(MINUTE_WINDOW_S * (0.9 + random.random() / 5))

    delay: float = min(BASE_DELAY_S * (2.0 ** (attempt - 1)), MAX_DELAY_S)
    return float(delay * (0.5 + random.random() / 2))


def _minute_bucket_empty(header: Any) -> bool:
    """Whether the provider reports a per-minute allowance of zero.

    Matched by suffix rather than exact name because providers spell these
    differently (`tokens-minute`, `requests-minute`) and the question is the
    same for all of them: is the thing being rationed per minute, and is there
    none of it left.
    """
    if not hasattr(header, "items"):
        return False
    for key, value in header.items():
        name = str(key).lower()
        if name.startswith("x-ratelimit-remaining-") and name.endswith("-minute"):
            try:
                if float(value) <= 0:
                    return True
            except (TypeError, ValueError):
                continue
    return False  # noqa: S311 — jitter, not crypto


async def ainvoke_with_rate_limit_retry(runnable: Any, *args: Any, **kwargs: Any) -> Any:
    """`runnable.ainvoke(...)`, retrying only on a provider 429."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return await runnable.ainvoke(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 — re-raised unless it is a 429
            if not is_rate_limit(exc) or attempt == MAX_ATTEMPTS:
                raise
            delay = retry_after_seconds(exc, attempt)
            logger.warning(
                "provider rate limit; waiting %.1fs and retrying "
                "[attempt %d/%d]",
                delay,
                attempt,
                MAX_ATTEMPTS,
            )
            await asyncio.sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover
