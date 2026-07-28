"""Secrets must not survive a trip through an error message.

The cases here are the ones that actually occur: asyncpg puts the DSN in a
connection error, provider clients echo keys and Authorization headers, and git
puts credentials in a remote URL. Each assertion checks two things — that the
secret is gone, and that enough of the sentence survives to still be a
diagnostic.
"""

from __future__ import annotations

import pytest

from app.exceptions import CloneError, RepoNotFoundError
from app.redact import MAX_LEN, redact, safe_error_text


@pytest.mark.parametrize(
    ("raw", "gone", "kept"),
    [
        (
            'connection to "postgresql://app:hunter2@db.internal:5432/r1" failed',
            "hunter2",
            "db.internal",
        ),
        ("redis://:s3cr3t@cache:6379 refused", "s3cr3t", "cache:6379"),
        ("401 from provider (api_key=sk-ant-abcdef0123456789)", "abcdef0123456789", "401"),
        ("google rejected AIzaSyD-1234567890abcdefg", "AIzaSyD-1234567890abcdefg", "google"),
        ("sent Authorization: Bearer eyJhbGciOi.J9", "eyJhbGciOi.J9", "Authorization"),
        ('config had password: "letmein"', "letmein", "config"),
        ("token=ghp_0123456789abcdef", "ghp_0123456789abcdef", "token"),
        ("no such file /home/rachit/.ssh/id_rsa", "rachit", ".ssh"),
    ],
)
def test_redact_removes_the_secret_and_keeps_the_sentence(
    raw: str, gone: str, kept: str
) -> None:
    out = redact(raw)
    assert gone not in out
    assert kept in out


def test_redact_collapses_a_traceback_to_one_line_and_caps_length() -> None:
    out = redact("line one\n  line two\n" + "x" * 5_000)
    assert "\n" not in out
    assert len(out) <= MAX_LEN


def test_ordinary_identifiers_survive() -> None:
    """Redaction must not eat the ids that make an error useful."""
    sha = "4db16f6a1b2c3d4e5f60718293a4b5c6d7e8f900"
    out = redact(f"repo 11111111-1111-1111-1111-111111111111 at {sha} has no files")
    assert sha in out
    assert "11111111-1111-1111-1111-111111111111" in out


def test_our_own_errors_are_shown_as_written() -> None:
    """AppError messages are authored for a reader; no type noise by default."""
    assert safe_error_text(RepoNotFoundError("abc")) == "no repo abc"


def test_unfamiliar_errors_carry_their_type() -> None:
    assert safe_error_text(ValueError("bad input")) == "ValueError: bad input"


def test_include_type_forces_the_prefix_for_operator_fields() -> None:
    """`repos.error` triage starts from the class name (see app/worker.py)."""
    text = safe_error_text(
        CloneError("failed to clone: repository not found"), include_type=True
    )
    assert text == "CloneError: failed to clone: repository not found"


def test_a_secret_inside_one_of_our_own_errors_is_still_redacted() -> None:
    """Authoring the message is not a promise that what it interpolates is safe."""
    out = safe_error_text(CloneError("git failed on https://x:tok3n@github.com/a/b"))
    assert "tok3n" not in out
    assert "github.com" in out
