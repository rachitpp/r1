"""The GitHub OAuth exchange (SPEC §13.3).

A security boundary that had no direct tests: every case here is a way the
exchange can go wrong, and two of them are ways it can go wrong *while GitHub
answers 200*. The network is stubbed at the transport, so the real request
construction, status handling and body parsing all run.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.auth import github
from app.config import get_settings
from app.exceptions import AuthNotConfiguredError, OAuthError

Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Credentials present, so `_credentials()` does not short-circuit."""
    settings = get_settings()
    monkeypatch.setattr(settings, "GITHUB_CLIENT_ID", "client-id")
    monkeypatch.setattr(settings, "GITHUB_CLIENT_SECRET", "client-secret")
    monkeypatch.setattr(settings, "SESSION_SECRET", "session-secret")


def _github(monkeypatch: pytest.MonkeyPatch, handler: Handler) -> list[httpx.Request]:
    """Point the module's httpx client at ``handler``; record what it sent."""
    seen: list[httpx.Request] = []
    real = httpx.AsyncClient

    def _record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    def _fake(**kwargs: Any) -> httpx.AsyncClient:
        kwargs.pop("transport", None)
        return real(transport=httpx.MockTransport(_record), **kwargs)

    monkeypatch.setattr(github.httpx, "AsyncClient", _fake)
    return seen


def _json(payload: dict[str, Any], status: int = 200) -> Handler:
    return lambda _request: httpx.Response(status, json=payload)


# --- exchange_code ----------------------------------------------------------


async def test_exchange_returns_the_access_token(
    configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _github(monkeypatch, _json({"access_token": "gho_secret"}))

    assert await github.exchange_code("the-code", "http://cb") == "gho_secret"

    (request,) = seen
    assert str(request.url) == github.GITHUB_TOKEN_URL
    assert request.headers["Accept"] == "application/json"
    body = request.content.decode()
    assert "code=the-code" in body
    assert "client_secret=client-secret" in body


async def test_a_200_carrying_an_error_is_still_a_failure(
    configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GitHub answers 200 with ``{"error": ...}``, so status alone is not the check."""
    _github(monkeypatch, _json({"error": "bad_verification_code"}))

    with pytest.raises(OAuthError):
        await github.exchange_code("stale", "http://cb")


async def test_github_error_description_never_reaches_the_caller(
    configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`error_description` can name the client id — it belongs in the log only."""
    _github(
        monkeypatch,
        _json(
            {
                "error": "incorrect_client_credentials",
                "error_description": "client_id=client-id is not valid",
            }
        ),
    )

    with pytest.raises(OAuthError) as caught:
        await github.exchange_code("code", "http://cb")
    assert "client-id" not in str(caught.value)


async def test_a_non_200_is_a_failure(
    configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _github(monkeypatch, _json({}, status=503))

    with pytest.raises(OAuthError, match="503"):
        await github.exchange_code("code", "http://cb")


async def test_a_200_with_no_token_is_a_failure(
    configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _github(monkeypatch, _json({"token_type": "bearer"}))

    with pytest.raises(OAuthError, match="no access token"):
        await github.exchange_code("code", "http://cb")


async def test_an_unreachable_github_is_an_oauth_error_not_a_transport_error(
    configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    _github(monkeypatch, _boom)

    with pytest.raises(OAuthError, match="could not reach GitHub"):
        await github.exchange_code("code", "http://cb")


# --- fetch_user -------------------------------------------------------------


async def test_fetch_user_keeps_only_the_four_identity_fields(
    configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Everything else GitHub returns is dropped at the boundary (§13.2)."""
    seen = _github(
        monkeypatch,
        _json(
            {
                "id": 4242,
                "login": "octocat",
                "name": "The Octocat",
                "avatar_url": "https://example.test/a.png",
                "email": "secret@example.test",
                "company": "GitHub",
            }
        ),
    )

    user = await github.fetch_user("gho_secret")

    assert user == github.GitHubUser(
        github_id=4242,
        login="octocat",
        name="The Octocat",
        avatar_url="https://example.test/a.png",
    )
    assert not hasattr(user, "email")
    (request,) = seen
    assert request.headers["Authorization"] == "Bearer gho_secret"


async def test_fetch_user_tolerates_a_missing_name_and_avatar(
    configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _github(monkeypatch, _json({"id": 1, "login": "ghost"}))

    user = await github.fetch_user("token")

    assert (user.name, user.avatar_url) == (None, None)


@pytest.mark.parametrize(
    "body",
    [
        {"login": "octocat"},  # no id
        {"id": 1},  # no login
        {"id": "1", "login": "octocat"},  # id is not an int
    ],
)
async def test_an_unrecognizable_user_is_refused(
    configured: None, monkeypatch: pytest.MonkeyPatch, body: dict[str, Any]
) -> None:
    _github(monkeypatch, _json(body))

    with pytest.raises(OAuthError, match="unrecognizable"):
        await github.fetch_user("token")


async def test_a_non_200_user_read_is_a_failure(
    configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _github(monkeypatch, _json({}, status=401))

    with pytest.raises(OAuthError, match="401"):
        await github.fetch_user("expired")


# --- authorize_url and configuration ---------------------------------------


def test_authorize_url_carries_state_scope_and_redirect(configured: None) -> None:
    url = github.authorize_url("the-state", "http://cb/auth")

    assert url.startswith(github.GITHUB_AUTHORIZE_URL + "?")
    assert "state=the-state" in url
    assert "client_id=client-id" in url
    assert "allow_signup=false" in url


def test_unconfigured_auth_is_named_rather_than_attempted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without credentials this must say so, not fail later inside a request."""
    monkeypatch.setattr(get_settings(), "GITHUB_CLIENT_ID", None)

    with pytest.raises(AuthNotConfiguredError):
        github.authorize_url("state", "http://cb")
