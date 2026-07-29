"""Cross-tenant isolation (SPEC §13.5) — the negative suite.

Every test here is written from the *stranger's* side: a signed-in user who
owns nothing asking for someone else's repo, and an anonymous caller asking for
anything. Before V1 all of these succeeded, because `_require_repo` checked
that a repo existed and never who it belonged to, and `GET /repos` returned the
whole table.

The assertion is **404, not 403**, everywhere. A 403 would confirm the id names
a real repo, which is the fact being protected — a stranger must not be able to
tell "not yours" from "does not exist".
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.api.middleware import client_identity
from app.auth import tokens
from app.config import SESSION_COOKIE, get_settings

from .conftest import (
    FILE_PATH,
    INDEXING_REPO_ID,
    OTHER_USER_ID,
    REPO_ID,
    UNKNOWN_REPO_ID,
    USER_ID,
)

# Every route that takes a repo id, as (method, path template).
REPO_SCOPED = [
    ("GET", "/repos/{id}"),
    ("GET", "/repos/{id}/files?path=" + FILE_PATH),
    ("POST", "/repos/{id}/chat"),
]


def _url(template: str, repo_id: object) -> str:
    return template.format(id=repo_id)


async def _call(client: httpx.AsyncClient, method: str, url: str) -> httpx.Response:
    if method == "POST":
        return await client.post(url, json={"question": "how does auth work?"})
    return await client.get(url)


@pytest.mark.parametrize(("method", "template"), REPO_SCOPED)
async def test_a_stranger_cannot_reach_someone_elses_repo(
    other_client: httpx.AsyncClient, method: str, template: str
) -> None:
    """The whole point of V1: a real, ready repo owned by someone else."""
    response = await _call(other_client, method, _url(template, REPO_ID))
    assert response.status_code == 404


@pytest.mark.parametrize(("method", "template"), REPO_SCOPED)
async def test_an_unowned_repo_is_indistinguishable_from_a_missing_one(
    other_client: httpx.AsyncClient, method: str, template: str
) -> None:
    """404 for "not yours" and for "no such repo", with the same message shape.

    Any difference between these two responses would be a repo existence oracle
    — exactly what returning 404 instead of 403 exists to deny. The detail does
    echo the requested id, which carries no information: the caller chose it.
    So the property is that the message is *only* that echo, identical once the
    id is substituted.
    """
    unowned = await _call(other_client, method, _url(template, REPO_ID))
    missing = await _call(other_client, method, _url(template, UNKNOWN_REPO_ID))
    assert unowned.status_code == missing.status_code == 404
    assert unowned.json()["detail"] == f"no repo {REPO_ID}"
    assert missing.json()["detail"] == f"no repo {UNKNOWN_REPO_ID}"
    assert unowned.json().keys() == missing.json().keys()


async def test_a_stranger_sees_an_empty_library(
    other_client: httpx.AsyncClient,
) -> None:
    """`GET /repos` is the caller's library, not the table (§13.6)."""
    response = await other_client.get("/repos")
    assert response.status_code == 200
    assert response.json()["repos"] == []


async def test_the_owner_still_sees_their_repos(client: httpx.AsyncClient) -> None:
    """The mirror of the test above — isolation that hides everything is easy."""
    response = await client.get("/repos")
    assert response.status_code == 200
    ids = {r["id"] for r in response.json()["repos"]}
    assert str(REPO_ID) in ids
    assert str(INDEXING_REPO_ID) in ids


async def test_chat_on_an_unowned_repo_is_refused_before_readiness(
    other_client: httpx.AsyncClient,
) -> None:
    """Ownership is checked before status, so 404 wins over 409.

    Ordering matters: answering 409 "repo not ready" for a stranger's indexing
    repo would confirm the repo exists *and* leak its state.
    """
    response = await other_client.post(
        f"/repos/{INDEXING_REPO_ID}/chat", json={"question": "hello?"}
    )
    assert response.status_code == 404


@pytest.mark.parametrize(("method", "template"), [*REPO_SCOPED, ("GET", "/repos")])
async def test_every_repo_route_requires_a_session(
    anon_client: httpx.AsyncClient, method: str, template: str
) -> None:
    """No session, no access — checked against the real dependency."""
    response = await _call(anon_client, method, _url(template, REPO_ID))
    assert response.status_code == 401


async def test_creating_a_repo_requires_a_session(
    anon_client: httpx.AsyncClient,
) -> None:
    response = await anon_client.post(
        "/repos", json={"url": "https://github.com/owner/new"}
    )
    assert response.status_code == 401


async def test_auth_me_requires_a_session(anon_client: httpx.AsyncClient) -> None:
    assert (await anon_client.get("/auth/me")).status_code == 401


async def test_auth_me_returns_the_signed_in_user(client: httpx.AsyncClient) -> None:
    response = await client.get("/auth/me")
    assert response.status_code == 200
    body = response.json()
    assert body["login"] == "owner"
    # §13.2: github_id is the join key and has no business in the browser.
    assert "github_id" not in body


async def test_health_and_ready_stay_open(anon_client: httpx.AsyncClient) -> None:
    """Operational endpoints are not behind the session (§13.6).

    A liveness probe that needs credentials is a liveness probe that reports the
    process down whenever auth is misconfigured.
    """
    assert (await anon_client.get("/health")).status_code == 200
    assert (await anon_client.get("/ready")).status_code in (200, 503)


# ---------------------------------------------------------------------------
# Rate-limit identity (SPEC §13.6)
# ---------------------------------------------------------------------------
#
# An IP is a poor identity for a quota: a corporate NAT or a mobile carrier puts
# hundreds of unrelated people in one bucket, so one heavy user throttles a
# building. These pin that the limiter counts *users* once there is one, and
# still counts IPs when there is not.

SECRET = "rate-limit-identity-test-secret"


def _scope(*, cookie: str | None = None, bearer: str | None = None) -> dict[str, Any]:
    """A minimal ASGI scope — `client_identity` runs below FastAPI."""
    headers: list[tuple[bytes, bytes]] = []
    if cookie is not None:
        headers.append((b"cookie", f"{SESSION_COOKIE}={cookie}".encode()))
    if bearer is not None:
        headers.append((b"authorization", f"Bearer {bearer}".encode()))
    return {"type": "http", "headers": headers, "client": ("203.0.113.7", 51234)}


@pytest.fixture
def session_secret(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(get_settings(), "SESSION_SECRET", SECRET)
    return SECRET


def test_the_rate_limit_identity_is_the_user_when_signed_in(
    session_secret: str,
) -> None:
    token = tokens.issue(USER_ID, session_secret)
    identity = client_identity(_scope(cookie=token), trust_proxy=False)
    assert identity == f"user:{USER_ID}"


def test_a_bearer_token_identifies_the_same_user_as_the_cookie(
    session_secret: str,
) -> None:
    """The CLIs and tests use the header; both must land in one bucket."""
    token = tokens.issue(USER_ID, session_secret)
    assert client_identity(_scope(bearer=token), trust_proxy=False) == client_identity(
        _scope(cookie=token), trust_proxy=False
    )


def test_two_users_from_one_ip_get_separate_buckets(session_secret: str) -> None:
    """The whole point: shared egress must not mean a shared quota."""
    first = client_identity(
        _scope(cookie=tokens.issue(USER_ID, session_secret)), trust_proxy=False
    )
    second = client_identity(
        _scope(cookie=tokens.issue(OTHER_USER_ID, session_secret)), trust_proxy=False
    )
    assert first != second


def test_an_anonymous_caller_is_counted_by_ip(session_secret: str) -> None:
    """`/auth/*` has no user yet, which is what keeps sign-in itself limited."""
    assert client_identity(_scope(), trust_proxy=False) == "203.0.113.7"


def test_a_forged_token_does_not_mint_a_fresh_quota(session_secret: str) -> None:
    """A token is *verified* before it is believed as an identity.

    Parsing the subject without checking the signature would let anyone reset
    their own limit by editing a cookie — a rate limiter that trusts the
    rate-limited is not one. Forged input falls back to the IP.
    """
    forged = tokens.issue(USER_ID, "some-other-secret")
    assert client_identity(_scope(cookie=forged), trust_proxy=False) == "203.0.113.7"


def test_a_junk_cookie_does_not_raise(session_secret: str) -> None:
    """Cookies are attacker-controlled; an exception here is a 500 on demand."""
    for junk in ("", "not-a-token", "v1.@@@.###.$$$"):
        assert client_identity(_scope(cookie=junk), trust_proxy=False) == "203.0.113.7"


async def test_a_second_user_submitting_a_known_url_joins_it(
    client: httpx.AsyncClient, other_client: httpx.AsyncClient, conn: object
) -> None:
    """A repo is a singleton keyed by URL, so the second submitter joins (§13.6).

    Before: the stranger cannot see it. After submitting the same URL: they can,
    and the owner still can — one corpus, two libraries, no re-ingest.
    """
    url = "https://github.com/owner/ready"
    assert (await other_client.get(f"/repos/{REPO_ID}")).status_code == 404

    response = await other_client.post("/repos", json={"url": url})
    assert response.status_code == 200  # joined an existing row, not created
    assert response.json()["id"] == str(REPO_ID)

    assert (await other_client.get(f"/repos/{REPO_ID}")).status_code == 200
    assert (await client.get(f"/repos/{REPO_ID}")).status_code == 200


# ---------------------------------------------------------------------------
# CLI ingests need an owner too (SPEC §13.5)
# ---------------------------------------------------------------------------


def test_resolve_owner_id_prefers_an_explicit_login() -> None:
    """`--owner <login>` names the library the repo lands in."""
    import asyncio

    from app.db import queries

    from .conftest import FakeConn

    conn = FakeConn()
    assert asyncio.run(queries.resolve_owner_id(conn, "owner")) == USER_ID
    assert asyncio.run(queries.resolve_owner_id(conn, "stranger")) == OTHER_USER_ID


def test_resolve_owner_id_falls_back_to_the_bootstrap_operator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No `--owner`: the repo joins the same library §13.7 hands the old ones to."""
    import asyncio

    from app.db import queries

    from .conftest import FakeConn

    conn = FakeConn()
    monkeypatch.setattr(get_settings(), "BOOTSTRAP_GITHUB_ID", 1)
    assert asyncio.run(queries.resolve_owner_id(conn)) == USER_ID


def test_resolve_owner_id_is_none_when_there_is_nobody_to_own_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`None` is the signal the CLI warns on, rather than writing a repo that is
    invisible to `GET /repos` and 404s on every route, for everyone."""
    import asyncio

    from app.db import queries

    from .conftest import FakeConn

    conn = FakeConn()
    monkeypatch.setattr(get_settings(), "BOOTSTRAP_GITHUB_ID", None)
    assert asyncio.run(queries.resolve_owner_id(conn)) is None
    assert asyncio.run(queries.resolve_owner_id(conn, "nobody")) is None
