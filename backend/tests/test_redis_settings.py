"""Queue connection settings (DECISIONS 2026-08-02).

These exist because the worker died three times on Redis errors that nothing
retried, and because the fix had a failure mode that a connection smoke test
cannot see: the *sync* ``redis.retry.Retry`` attaches happily and only breaks
when a retry actually fires. So this asserts the policy's behaviour, not just
its presence.
"""

from __future__ import annotations

import inspect

import pytest
from redis.asyncio.retry import Retry as AsyncRetry
from redis.exceptions import ConnectionError as RedisConnectionError

from app.config import get_settings, redis_settings


def test_conn_timeout_is_not_arq_lan_default() -> None:
    """ARQ's 1s default is shorter than a managed Redis takes to connect."""
    assert redis_settings().conn_timeout > 1


def test_connection_error_is_retried_not_just_timeout() -> None:
    """`retry_on_timeout` does not cover a peer reset; that needs naming."""
    rs = redis_settings()
    assert rs.retry_on_timeout is True
    assert RedisConnectionError in (rs.retry_on_error or [])


def test_retry_is_the_async_class() -> None:
    """The sync class of the same name is not awaitable and fails mid-incident.

    Guarding the import, not the behaviour below, because this is the mistake
    that is easy to reintroduce and impossible to notice in a smoke test.
    """
    retry = redis_settings().retry
    assert isinstance(retry, AsyncRetry)
    assert inspect.iscoroutinefunction(retry.call_with_retry)


@pytest.mark.asyncio
async def test_retry_recovers_from_a_transient_connection_error() -> None:
    rs = redis_settings()
    attempts = 0

    async def flaky() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RedisConnectionError("simulated peer reset")
        return "recovered"

    async def on_fail(_: Exception) -> None:
        return None

    assert await rs.retry.call_with_retry(flaky, on_fail) == "recovered"
    assert attempts == 3


@pytest.mark.asyncio
async def test_retry_gives_up_rather_than_looping_forever() -> None:
    """A Redis that is genuinely gone must still surface, and quickly."""
    rs = redis_settings()
    attempts = 0

    async def always_fails() -> str:
        nonlocal attempts
        attempts += 1
        raise RedisConnectionError("permanent")

    async def on_fail(_: Exception) -> None:
        return None

    with pytest.raises(RedisConnectionError):
        await rs.retry.call_with_retry(always_fails, on_fail)
    # One initial call plus the configured retries, and no more.
    assert attempts == get_settings().REDIS_COMMAND_RETRIES + 1
