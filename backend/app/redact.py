"""Making exception text safe to show a user.

Two places in this application put raw exception text somewhere a browser can
read it: the §9 ``error`` SSE event, and ``repos.error`` (which the dashboard
renders). Both are genuinely useful — "CloneError: repository not found" is the
answer to the user's question — and both are a leak, because the same field also
carries whatever an asyncpg or provider-client exception decided to include.
asyncpg puts the DSN in a connection error; a provider client can echo the
Authorization header it just sent.

The answer is redaction rather than suppression. Replacing every message with
"internal error" would be safe and would also delete the only diagnostic an
operator has for a failed ingest. So: strip the shapes that carry secrets, keep
the sentence.

Redaction is a backstop, not a boundary. Errors we author ourselves
(:class:`app.exceptions.AppError` and its subclasses) are written to be shown,
and pass through unchanged apart from a length cap.
"""

from __future__ import annotations

import re

from app.exceptions import AppError

# Ordered, and applied in this order. Each pattern keeps enough shape for the
# message to stay readable — a redacted DSN still says which host failed.
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Credentials embedded in a URL: postgres://user:pw@host, redis://:pw@host.
    (re.compile(r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*://)[^/\s:@]*:[^/\s@]*@"),
     r"\g<scheme>***:***@"),
    # Provider key shapes, matched by prefix so ordinary hex ids survive.
    (re.compile(r"\bsk-[A-Za-z0-9\-_]{8,}"), "sk-***"),
    (re.compile(r"\bAIza[0-9A-Za-z\-_]{10,}"), "AIza***"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{10,}"), "gh*_***"),
    # `Authorization: Bearer xyz`, and bare bearer tokens in a message.
    (re.compile(r"(?i)\bbearer\s+\S+"), "Bearer ***"),
    # key=value / key: value for anything that names itself a secret.
    (re.compile(
        r"(?i)\b(api[_-]?key|apikey|access[_-]?token|auth[_-]?token|token|secret"
        r"|password|passwd|credentials?)\b(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|\S+)"
    ), r"\1\2***"),
    # Home directories name the operator and the host's layout.
    (re.compile(r"/(home|Users)/[^/\s\"']+"), r"/\1/***"),
)

# Long enough to keep a stack-free asyncpg or provider message intact; short
# enough that a multi-kilobyte traceback never reaches a browser or a DB column.
MAX_LEN = 400


def redact(text: str, *, limit: int = MAX_LEN) -> str:
    """Strip secret-shaped substrings from ``text`` and cap its length."""
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    text = " ".join(text.split())  # collapse tracebacks/newlines to one line
    return text if len(text) <= limit else text[: limit - 1] + "…"


def safe_error_text(
    exc: BaseException, *, include_type: bool | None = None, limit: int = MAX_LEN
) -> str:
    """A one-liner for ``exc`` that is safe to show.

    Our own errors keep their wording — they were written to be read. Anything
    else is prefixed with its type, which for an unfamiliar failure is the
    informative half, and its message is redacted either way.

    ``include_type`` forces that prefix on or off. Operator-facing fields want
    it on even for our own errors: ``repos.error`` is the first thing anyone
    triaging a failed ingest looks at, and ``CloneError`` versus
    ``TooManyFilesError`` is the distinction they are looking for.
    """
    if include_type is None:
        include_type = not isinstance(exc, AppError)
    detail = redact(str(exc), limit=limit)
    if not include_type:
        return detail
    return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__
