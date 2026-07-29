"""The GitHub OAuth dance (SPEC §13.3).

Three steps, two of them network calls:

1. Send the browser to GitHub with a `state` we generated.
2. GitHub sends it back with a `code`; exchange that for an access token.
3. Read the identity the token grants.

Written out rather than pulled in — `authlib` is the obvious dependency and
rule 11 says ask, and this is ~60 lines against an HTTP client the project
already had (DECISIONS 2026-07-29, following the same call made for `slowapi`
and `prometheus-client` on 2026-07-28).

The access token is used once, here, and never stored. V1 reads an identity and
nothing else; the `read:user` scope cannot do more than that anyway. Keeping it
would be storing a live credential for every user in exchange for no feature.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from app.config import (
    GITHUB_API_USER_URL,
    GITHUB_AUTHORIZE_URL,
    GITHUB_SCOPES,
    GITHUB_TOKEN_URL,
    get_settings,
)
from app.exceptions import AuthNotConfiguredError, OAuthError

logger = logging.getLogger(__name__)

# GitHub is a third party on the critical path of a page load. Bound it, or a
# hung TLS connection holds a request worker until something else gives up.
HTTP_TIMEOUT_S = 10.0


@dataclass(frozen=True)
class GitHubUser:
    """The identity fields §13.2 stores. Everything else GitHub returns is
    dropped at the boundary rather than carried around and stored by accident."""

    github_id: int
    login: str
    name: str | None
    avatar_url: str | None


def _credentials() -> tuple[str, str]:
    settings = get_settings()
    if not settings.auth_configured:
        raise AuthNotConfiguredError(
            "GitHub sign-in is not configured: set GITHUB_CLIENT_ID, "
            "GITHUB_CLIENT_SECRET and SESSION_SECRET in backend/.env"
        )
    # auth_configured proved both are set; mypy cannot see through the property.
    assert settings.GITHUB_CLIENT_ID and settings.GITHUB_CLIENT_SECRET
    return settings.GITHUB_CLIENT_ID, settings.GITHUB_CLIENT_SECRET


def authorize_url(state: str, redirect_uri: str) -> str:
    """Where to send the browser to start sign-in (§13.3)."""
    client_id, _ = _credentials()
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": GITHUB_SCOPES,
            "state": state,
            "allow_signup": "false",
        }
    )
    return f"{GITHUB_AUTHORIZE_URL}?{query}"


async def exchange_code(code: str, redirect_uri: str) -> str:
    """Trade the callback's ``code`` for an access token.

    GitHub answers 200 with ``{"error": ...}`` when the exchange fails, so the
    status code alone is not the check — reading the body is.
    """
    client_id, client_secret = _credentials()
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_S) as client:
        try:
            response = await client.post(
                GITHUB_TOKEN_URL,
                headers={"Accept": "application/json"},
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
            )
        except httpx.HTTPError as exc:
            raise OAuthError(f"could not reach GitHub: {type(exc).__name__}") from exc

    if response.status_code != 200:
        raise OAuthError(f"GitHub token endpoint returned {response.status_code}")
    body = response.json()
    if "error" in body:
        # `error_description` is GitHub's, not ours, and can name the client id.
        # It belongs in the log; the caller gets the generic OAuthError message.
        logger.warning("github token exchange failed: %s", body.get("error"))
        raise OAuthError("GitHub rejected the sign-in attempt")
    token = body.get("access_token")
    if not isinstance(token, str) or not token:
        raise OAuthError("GitHub returned no access token")
    return token


async def fetch_user(access_token: str) -> GitHubUser:
    """Read the identity behind ``access_token`` (§13.3)."""
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_S) as client:
        try:
            response = await client.get(
                GITHUB_API_USER_URL,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/vnd.github+json",
                },
            )
        except httpx.HTTPError as exc:
            raise OAuthError(f"could not reach GitHub: {type(exc).__name__}") from exc

    if response.status_code != 200:
        raise OAuthError(f"GitHub user endpoint returned {response.status_code}")
    body = response.json()
    github_id = body.get("id")
    login = body.get("login")
    if not isinstance(github_id, int) or not isinstance(login, str):
        raise OAuthError("GitHub returned an unrecognizable user")
    return GitHubUser(
        github_id=github_id,
        login=login,
        name=body.get("name"),
        avatar_url=body.get("avatar_url"),
    )
