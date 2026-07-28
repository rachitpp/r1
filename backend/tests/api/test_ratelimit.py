"""Rule matching, the fixed window, and the concurrency gate.

Unit level: no app, no Redis. The behaviours worth pinning are the ones that
would fail silently — a rule table that stops matching the chat path, a limiter
that fails *closed* when Redis is down, a gate that leaks slots.
"""

from __future__ import annotations

import re

import pytest

from app.api.ratelimit import RedisRateLimiter, Rule, Slots, match_rule, rules_for
from app.config import get_settings

RULES = rules_for(get_settings())


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("POST", "/repos", "ingest"),
        ("POST", "/repos/", "ingest"),
        ("GET", "/repos", "default"),  # listing is a read; not the ingest limit
        (
            "POST",
            "/repos/11111111-1111-1111-1111-111111111111/chat",
            "chat",
        ),
        ("GET", "/repos/11111111-1111-1111-1111-111111111111", "default"),
        ("GET", "/repos/abc/files", "default"),
    ],
)
def test_rule_table_routes_each_endpoint_to_its_limit(
    method: str, path: str, expected: str
) -> None:
    rule = match_rule(RULES, method, path)
    assert rule is not None
    assert rule.name == expected


@pytest.mark.parametrize("path", ["/health", "/ready", "/metrics"])
def test_operational_endpoints_are_exempt(path: str) -> None:
    """Rate-limiting your own health check is how a healthy node gets evicted."""
    assert match_rule(RULES, "GET", path) is None


class FakeRedis:
    """Counts, and optionally fails, the two commands the limiter uses."""

    def __init__(self, *, fail: bool = False) -> None:
        self.values: dict[str, int] = {}
        self.expires: list[tuple[str, int]] = []
        self.fail = fail

    async def incr(self, name: str) -> int:
        if self.fail:
            raise ConnectionError("redis is gone")
        self.values[name] = self.values.get(name, 0) + 1
        return self.values[name]

    async def expire(self, name: str, time: int) -> bool:
        self.expires.append((name, time))
        return True


def _make(limit: int, window: int) -> Rule:
    """A rule matching everything, so these tests exercise the counter alone."""
    return Rule("t", "*", re.compile(".*"), limit, window)


async def test_requests_are_allowed_up_to_the_limit_then_refused() -> None:
    redis = FakeRedis()
    limiter = RedisRateLimiter(redis)
    rule = _make(limit=2, window=60)

    assert await limiter.check(rule, "1.2.3.4") is None
    assert await limiter.check(rule, "1.2.3.4") is None
    retry_after = await limiter.check(rule, "1.2.3.4")
    assert retry_after is not None and 0 < retry_after <= 60


async def test_limits_are_per_identity() -> None:
    limiter = RedisRateLimiter(FakeRedis())
    rule = _make(limit=1, window=60)

    assert await limiter.check(rule, "1.1.1.1") is None
    assert await limiter.check(rule, "2.2.2.2") is None
    assert await limiter.check(rule, "1.1.1.1") is not None


async def test_expiry_is_set_once_per_window_not_per_request() -> None:
    """One Redis command per request in the steady state (command budget)."""
    redis = FakeRedis()
    rule = _make(limit=10, window=60)
    for _ in range(4):
        await RedisRateLimiter(redis).check(rule, "1.2.3.4")
    assert len(redis.expires) == 1
    assert redis.expires[0][1] == 60


async def test_an_unreachable_redis_fails_open() -> None:
    """A queue outage must not also become a read outage."""
    limiter = RedisRateLimiter(FakeRedis(fail=True))
    assert await limiter.check(_make(limit=1, window=60), "1.2.3.4") is None


async def test_no_redis_at_all_allows_everything() -> None:
    limiter = RedisRateLimiter(None)
    assert await limiter.check(_make(limit=0, window=60), "1.2.3.4") is None


def test_slots_hands_out_exactly_its_limit() -> None:
    slots = Slots(2)
    assert slots.try_acquire()
    assert slots.try_acquire()
    assert not slots.try_acquire()
    assert slots.used == 2


def test_releasing_makes_a_slot_available_again() -> None:
    slots = Slots(1)
    assert slots.try_acquire()
    slots.release()
    assert slots.used == 0
    assert slots.try_acquire()


def test_over_release_is_clamped_rather_than_fatal() -> None:
    """A double release is a bug worth logging, never a reason to go down."""
    slots = Slots(1)
    slots.release()
    slots.release()
    assert slots.used == 0
    assert slots.try_acquire()
