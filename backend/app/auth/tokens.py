"""Opaque signed session tokens (SPEC §13.4).

Not a JWT. A JWT's value is that a third party can verify it without asking the
issuer; here the only verifier *is* the issuer, and the only claim is a user id.
What is left of JWT once that is removed is a header nobody reads and an
algorithm field that has been a vulnerability class of its own. So: four
base64url fields, HMAC-SHA256 over the first three.

    v1.<user_id>.<expires_at>.<signature>

Nothing here touches the database or the network, which is what makes the whole
module unit-testable without either.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from uuid import UUID

from app.config import SESSION_TTL_S

# Bumping this invalidates every token in circulation, which is the point: the
# format is part of the signed material, so a future change cannot be replayed
# against the old parser.
_VERSION = "v1"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(secret: str, payload: str) -> str:
    mac = hmac.new(secret.encode(), payload.encode(), hashlib.sha256)
    return _b64(mac.digest())


def new_state() -> str:
    """A random OAuth `state` value (§13.3)."""
    return secrets.token_urlsafe(32)


def constant_time_equals(a: str, b: str) -> bool:
    """Compare two secrets without leaking their prefix through timing.

    Used for the OAuth `state`. A plain ``==`` returns as soon as two bytes
    differ, so the time it takes reveals how much of a guess was right.
    """
    return hmac.compare_digest(a.encode(), b.encode())


def issue(user_id: UUID, secret: str, *, ttl_s: int = SESSION_TTL_S) -> str:
    """Mint a session token for ``user_id``, valid for ``ttl_s`` seconds."""
    expires_at = int(time.time()) + ttl_s
    payload = f"{_VERSION}.{_b64(user_id.bytes)}.{_b64(str(expires_at).encode())}"
    return f"{payload}.{_sign(secret, payload)}"


def verify(token: str, secret: str, *, now: float | None = None) -> UUID | None:
    """Return the token's user id, or ``None`` if it is not currently valid.

    One return value for every kind of failure — malformed, wrong version, bad
    signature, expired. A caller that could tell them apart would be able to
    tell a forged token from an expired one, and so would anyone probing the
    endpoint.

    The signature is checked with ``compare_digest`` before the expiry is read,
    so an attacker cannot learn anything by timing the comparison, and an
    unsigned expiry is never trusted.
    """
    parts = token.split(".")
    if len(parts) != 4:
        return None
    version, raw_id, raw_exp, signature = parts
    if version != _VERSION:
        return None

    payload = f"{version}.{raw_id}.{raw_exp}"
    if not hmac.compare_digest(_sign(secret, payload), signature):
        return None

    try:
        expires_at = int(_unb64(raw_exp).decode())
        user_id = UUID(bytes=_unb64(raw_id))
    except (ValueError, UnicodeDecodeError):
        # Signed but unparseable: only reachable if the secret leaked or a
        # previous version minted a different shape. Refuse either way.
        return None

    if (now if now is not None else time.time()) >= expires_at:
        return None
    return user_id
