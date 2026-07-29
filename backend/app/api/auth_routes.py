"""Sign-in routes (SPEC §13.3).

Thin, like every other route module: the OAuth mechanics live in
``app/auth/github.py``, the token format in ``app/auth/tokens.py``, and the
user rows in ``app/db/queries.py``.

The two redirects here are the only endpoints in the API that answer with a
302 rather than JSON, because the browser — not our frontend — is the client
for both halves of the dance.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request, Response, status
from fastapi.responses import RedirectResponse

from app.api.deps import Conn, CurrentUser
from app.api.schemas import UserOut
from app.auth import github, tokens
from app.config import (
    OAUTH_STATE_COOKIE,
    OAUTH_STATE_TTL_S,
    SESSION_COOKIE,
    SESSION_TTL_S,
    get_settings,
)
from app.db import queries
from app.exceptions import OAuthError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


def _callback_url(request: Request) -> str:
    """The redirect URI, derived from the request rather than configured.

    It must match what GitHub has registered *and* what was sent on the
    authorize call, so deriving it once from the live request is the way to
    keep those two in step across localhost, a forwarded port, and a
    deployment — three places a hardcoded value would be wrong in two.
    """
    return str(request.url_for("github_callback"))


def _secure_cookies() -> bool:
    """Whether to set ``Secure``. Off for plain-http local dev, or the browser
    silently drops every cookie we set and sign-in fails with no error."""
    return get_settings().FRONTEND_ORIGIN.startswith("https://")


@router.get("/github/login")
async def github_login(request: Request) -> RedirectResponse:
    """Start sign-in: remember a `state`, then hand off to GitHub (§13.3)."""
    state = tokens.new_state()
    response = RedirectResponse(
        github.authorize_url(state, _callback_url(request)),
        status_code=status.HTTP_302_FOUND,
    )
    response.set_cookie(
        OAUTH_STATE_COOKIE,
        state,
        max_age=OAUTH_STATE_TTL_S,
        httponly=True,
        samesite="lax",
        secure=_secure_cookies(),
    )
    return response


@router.get("/github/callback", name="github_callback")
async def github_callback(
    request: Request,
    conn: Conn,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """Finish sign-in: verify `state`, exchange the code, set the session.

    ``state`` is compared against the cookie with a constant-time comparison.
    Without this check an attacker can complete a sign-in in someone else's
    browser against the *attacker's* GitHub account — login CSRF — and every
    repo the victim then submits lands in the attacker's library.
    """
    settings = get_settings()
    expected = request.cookies.get(OAUTH_STATE_COOKIE)

    if error:
        # The user clicked "Cancel" on the consent screen. Not an error worth a
        # stack trace; send them back where they started.
        logger.info("github sign-in declined: %s", error)
        raise OAuthError("sign-in was cancelled")
    if not code or not state or not expected:
        raise OAuthError("sign-in did not complete")
    if not tokens.constant_time_equals(state, expected):
        logger.warning("oauth state mismatch on callback")
        raise OAuthError("sign-in did not complete")

    access_token = await github.exchange_code(code, _callback_url(request))
    profile = await github.fetch_user(access_token)

    # §13.7: hand the pre-auth placeholder its real identity before the upsert,
    # so the operator's first sign-in inherits every repo ingested before auth
    # existed instead of starting with an empty library beside an orphan row.
    if settings.BOOTSTRAP_GITHUB_ID == profile.github_id:
        await queries.adopt_bootstrap_user(conn, profile.github_id)

    user = await queries.upsert_user(
        conn,
        github_id=profile.github_id,
        login=profile.login,
        name=profile.name,
        avatar_url=profile.avatar_url,
    )
    assert settings.SESSION_SECRET  # github.exchange_code already required it
    session = tokens.issue(user["id"], settings.SESSION_SECRET)

    response = RedirectResponse(
        settings.FRONTEND_ORIGIN.split(",")[0], status_code=status.HTTP_302_FOUND
    )
    response.set_cookie(
        SESSION_COOKIE,
        session,
        max_age=SESSION_TTL_S,
        httponly=True,
        samesite="lax",
        secure=_secure_cookies(),
    )
    response.delete_cookie(OAUTH_STATE_COOKIE)
    logger.info("signed in %s (github_id=%s)", profile.login, profile.github_id)
    return response


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout() -> Response:
    """Clear the session cookie.

    Nothing server-side to revoke in V1: the token is stateless and stays valid
    until it expires (§13.4). Recorded there as the reason a `user_sessions`
    table is the next thing this needs, rather than left to be discovered.
    """
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(SESSION_COOKIE)
    return response


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> UserOut:
    """The signed-in user, or 401. What the frontend polls to know its state."""
    return UserOut.from_row(user)
