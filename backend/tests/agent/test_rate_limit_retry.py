"""Retrying a provider 429 (SPEC §7.2).

Found by running the app, not by testing it: a live question completed seven
tool calls and was then discarded whole by a single 429 on the eighth model
call. `max_retries=5` was configured on the client and a comment in `model.py`
claimed it covered rate limits. It never did — see the first test.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.agent.rate_limit_retry import (
    MAX_ATTEMPTS,
    MINUTE_WINDOW_S,
    ainvoke_with_rate_limit_retry,
    is_rate_limit,
    retry_after_seconds,
)


def _http_error(status: int, headers: dict[str, str] | None = None) -> Exception:
    request = httpx.Request("POST", "https://api.example.test/v1/chat")
    response = httpx.Response(status, headers=headers or {}, request=request)
    return httpx.HTTPStatusError("boom", request=request, response=response)


def test_the_clients_configured_retries_never_covered_429() -> None:
    """The reason this module exists, pinned as an assertion.

    `langchain_mistralai` retries `httpx.RequestError` and `httpx.StreamError`.
    A 429 raises `httpx.HTTPStatusError`, which is a *sibling* under
    `HTTPError` — so `max_retries=5` bought retries on connection failures and
    nothing on the most common free-tier failure.
    """
    assert not issubclass(httpx.HTTPStatusError, httpx.RequestError)
    assert not issubclass(httpx.HTTPStatusError, httpx.StreamError)


def test_recognises_a_429_and_nothing_else() -> None:
    assert is_rate_limit(_http_error(429)) is True
    assert is_rate_limit(_http_error(400)) is False
    assert is_rate_limit(_http_error(401)) is False
    assert is_rate_limit(RuntimeError("unrelated")) is False


def test_honours_retry_after_when_the_provider_sends_one() -> None:
    """The provider's own answer beats any curve invented here."""
    exc = _http_error(429, {"retry-after": "7"})
    assert retry_after_seconds(exc, attempt=1) == 7.0


def test_a_nonsense_retry_after_falls_back_to_backoff() -> None:
    exc = _http_error(429, {"retry-after": "whenever"})
    delay = retry_after_seconds(exc, attempt=1)
    assert 0 < delay <= 30.0


def test_backoff_is_jittered_and_capped() -> None:
    exc = _http_error(429)
    delays = {retry_after_seconds(exc, attempt=8) for _ in range(20)}
    assert len(delays) > 1, "identical delays would re-trigger the limit together"
    assert max(delays) <= 60.0


def test_an_empty_per_minute_bucket_waits_for_the_window() -> None:
    """Measured against Mistral, which sends no Retry-After but does send this.

    The first version waited ~2s then ~4s against a minute-long window: it
    burned both attempts and reported the same failure, only slower.
    """
    exc = _http_error(
        429,
        {
            "x-ratelimit-limit-tokens-minute": "25000",
            "x-ratelimit-remaining-tokens-minute": "0",
        },
    )
    delay = retry_after_seconds(exc, attempt=1)
    assert delay >= MINUTE_WINDOW_S * 0.9


def test_a_bucket_with_headroom_left_uses_ordinary_backoff() -> None:
    """Zero is the signal; a non-empty bucket means something else went wrong."""
    exc = _http_error(
        429,
        {
            "x-ratelimit-limit-tokens-minute": "25000",
            "x-ratelimit-remaining-tokens-minute": "9000",
        },
    )
    assert retry_after_seconds(exc, attempt=1) < MINUTE_WINDOW_S * 0.5


def test_retry_after_still_wins_over_the_header_heuristic() -> None:
    exc = _http_error(
        429,
        {"retry-after": "3", "x-ratelimit-remaining-tokens-minute": "0"},
    )
    assert retry_after_seconds(exc, attempt=1) == 3.0


class FakeRunnable:
    """Fails with the scripted exceptions, then succeeds."""

    def __init__(self, failures: list[Exception]) -> None:
        self.failures = failures
        self.calls = 0

    async def ainvoke(self, *_a: Any, **_k: Any) -> str:
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return "answer"


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _sleep(_: float) -> None:
        return None

    import app.agent.rate_limit_retry as mod

    monkeypatch.setattr(mod.asyncio, "sleep", _sleep)


async def test_a_transient_429_no_longer_discards_the_run() -> None:
    runnable = FakeRunnable([_http_error(429)])
    assert await ainvoke_with_rate_limit_retry(runnable, ["msg"]) == "answer"
    assert runnable.calls == 2


async def test_a_persistent_429_still_surfaces() -> None:
    """A quota problem is not made better by a fourth wait."""
    runnable = FakeRunnable([_http_error(429) for _ in range(MAX_ATTEMPTS)])
    with pytest.raises(httpx.HTTPStatusError):
        await ainvoke_with_rate_limit_retry(runnable, ["msg"])
    assert runnable.calls == MAX_ATTEMPTS


async def test_a_bad_request_is_not_retried() -> None:
    """A 400 will be bad again; retrying turns a clear error into a slow one."""
    runnable = FakeRunnable([_http_error(400)])
    with pytest.raises(httpx.HTTPStatusError):
        await ainvoke_with_rate_limit_retry(runnable, ["msg"])
    assert runnable.calls == 1


async def test_an_unrelated_error_is_not_retried() -> None:
    runnable = FakeRunnable([RuntimeError("bug in our code")])
    with pytest.raises(RuntimeError):
        await ainvoke_with_rate_limit_retry(runnable, ["msg"])
    assert runnable.calls == 1


async def test_a_successful_call_is_passed_straight_through() -> None:
    runnable = FakeRunnable([])
    assert await ainvoke_with_rate_limit_retry(runnable, ["msg"]) == "answer"
    assert runnable.calls == 1
