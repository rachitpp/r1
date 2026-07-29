"""Session token format (SPEC §13.4).

Pure functions over strings — no database, no network, no clock beyond an
injectable ``now``. Every test here is a forgery attempt or an expiry check,
because those are the only two ways a session token fails in a way that
matters.
"""

from __future__ import annotations

import time
import uuid

import pytest

from app.auth import tokens

SECRET = "test-secret-not-a-real-one"
OTHER_SECRET = "a-different-secret"
USER = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def test_a_freshly_issued_token_round_trips() -> None:
    assert tokens.verify(tokens.issue(USER, SECRET), SECRET) == USER


def test_a_token_signed_with_another_secret_is_refused() -> None:
    """Rotating SESSION_SECRET is the emergency lever; it must actually work."""
    assert tokens.verify(tokens.issue(USER, OTHER_SECRET), SECRET) is None


def test_an_expired_token_is_refused() -> None:
    """`issue` stamps a wall-clock expiry, so `now` has to be a real timestamp."""
    issued_at = time.time()
    token = tokens.issue(USER, SECRET, ttl_s=100)
    assert tokens.verify(token, SECRET, now=issued_at + 50) == USER
    assert tokens.verify(token, SECRET, now=issued_at + 101) is None


def test_expiry_cannot_be_extended_without_the_secret() -> None:
    """The expiry is inside the signed payload, not beside it.

    An attacker holding an expired token knows both the user id and the old
    expiry; the only thing stopping them re-dating it is that the signature
    covers it. Verified against a clock past the original expiry, so the only
    thing that can refuse this token is the signature check.
    """
    issued_at = time.time()
    version, raw_id, _raw_exp, signature = tokens.issue(
        USER, SECRET, ttl_s=1
    ).split(".")
    far_future = tokens._b64(str(int(issued_at) + 10**6).encode())
    forged = f"{version}.{raw_id}.{far_future}.{signature}"
    assert tokens.verify(forged, SECRET, now=issued_at + 100) is None


def test_the_user_id_cannot_be_swapped() -> None:
    """Privilege escalation by editing the subject — the signature covers it."""
    victim = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    version, _raw_id, raw_exp, signature = tokens.issue(USER, SECRET).split(".")
    forged = f"{version}.{tokens._b64(victim.bytes)}.{raw_exp}.{signature}"
    assert tokens.verify(forged, SECRET) is None


@pytest.mark.parametrize(
    "token",
    [
        "",
        "garbage",
        "a.b.c",  # too few fields
        "a.b.c.d.e",  # too many
        "v2.aaaa.bbbb.cccc",  # unknown version
        "v1...",  # right shape, empty fields
    ],
)
def test_malformed_tokens_are_refused_without_raising(token: str) -> None:
    """A cookie is attacker-controlled input; parsing it must never raise.

    An exception here would be a 500 on every request carrying a junk cookie,
    which is a denial of service anyone can trigger from a browser console.
    """
    assert tokens.verify(token, SECRET) is None


def test_two_tokens_for_the_same_user_are_not_reused_state() -> None:
    """Issuing is deterministic given the same second, which is fine and worth
    pinning: the token is a signed claim, not a nonce, and V1 keeps no
    server-side session table to collide with (§13.4)."""
    first = tokens.issue(USER, SECRET, ttl_s=3600)
    second = tokens.issue(USER, SECRET, ttl_s=3600)
    assert tokens.verify(first, SECRET) == tokens.verify(second, SECRET) == USER


def test_state_values_are_unpredictable_and_unique() -> None:
    values = {tokens.new_state() for _ in range(100)}
    assert len(values) == 100
    assert all(len(v) >= 32 for v in values)


def test_constant_time_equals_matches_only_identical_strings() -> None:
    assert tokens.constant_time_equals("abc", "abc")
    assert not tokens.constant_time_equals("abc", "abd")
    assert not tokens.constant_time_equals("abc", "abcd")
    assert not tokens.constant_time_equals("", "abc")
