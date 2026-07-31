"""`--json` on both CLIs: stdout is one document, and it is always parseable.

The contract these pin is narrow but load-bearing for anything scripting this
project: **stdout carries exactly one JSON object, on success and on failure**,
and every human-readable line goes to stderr. The failure half matters most —
a tool that emits clean JSON when things work and prose when they don't forces
every caller to parse both.
"""

from __future__ import annotations

import json

import pytest

from app.agent import cli as agent_cli
from app.exceptions import CloneError
from app.ingest import cli as ingest_cli
from app.ingest.chunker import Chunk
from app.ingest.cli import IngestResult
from app.ingest.filters import SelectionResult


def _result() -> IngestResult:
    chunks = [
        Chunk(
            file_path="pkg/auth.py",
            symbol="verify_token",
            kind="function",
            part=1,
            n_parts=1,
            start_line=1,
            end_line=2,
            header="# pkg/auth.py",
            code="def verify_token(token):\n    return True\n",
        ),
        Chunk(
            file_path="pkg/auth.py",
            symbol="Signer",
            kind="class",
            part=1,
            n_parts=2,
            start_line=5,
            end_line=40,
            header="# pkg/auth.py",
            code="class Signer:\n    pass\n",
        ),
    ]
    return IngestResult(
        name="owner/repo",
        head_sha="abc123",
        default_branch="main",
        selection=SelectionResult(
            files=[],
            n_candidates=10,
            skipped_non_python=3,
            skipped_ignored_dir=1,
            skipped_too_large=0,
            skipped_binary=0,
            skipped_decode_error=0,
        ),
        chunks=chunks,
        n_syntax_errors=1,
        elapsed_s=1.2345,
    )


def test_ingest_json_is_one_parseable_object_on_stdout(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(ingest_cli, "ingest", lambda url, strategy="ast": _result())

    assert ingest_cli.main(["https://github.com/owner/repo", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)  # raises if anything else leaked
    assert payload["ok"] is True
    assert payload["mode"] == "inspect"
    assert payload["head_sha"] == "abc123"
    assert payload["chunks"]["total"] == 2
    assert payload["chunks"]["by_kind"]["function"] == 1
    assert payload["chunks"]["oversize"] == 1
    assert payload["selection"]["skipped"]["non_python"] == 3


def test_ingest_json_reports_failure_as_json_and_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(url: str, strategy: str = "ast") -> IngestResult:
        raise CloneError("repository not found")

    monkeypatch.setattr(ingest_cli, "ingest", boom)

    assert ingest_cli.main(["https://github.com/owner/nope", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"]["type"] == "CloneError"
    assert "not found" in payload["error"]["message"]


def test_ingest_failure_without_json_stays_on_stderr(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The human path is unchanged: prose on stderr, nothing on stdout."""

    def boom(url: str, strategy: str = "ast") -> IngestResult:
        raise CloneError("repository not found")

    monkeypatch.setattr(ingest_cli, "ingest", boom)

    assert ingest_cli.main(["https://github.com/owner/nope"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "error: repository not found" in captured.err


def test_ingest_json_keeps_samples_off_stdout(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--sample` prints code. In --json mode that must not enter the document.

    This is the regression the stdout/stderr split exists for: the sample text
    is not JSON, so a single stray line makes correct output unparseable.
    """
    monkeypatch.setattr(ingest_cli, "ingest", lambda url, strategy="ast": _result())

    assert ingest_cli.main(["https://github.com/owner/repo", "--json", "--sample", "2"]) == 0

    captured = capsys.readouterr()
    json.loads(captured.out)  # stdout is still exactly one document
    assert "def verify_token" in captured.err


def test_ingest_json_records_the_dump_path(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path
) -> None:
    monkeypatch.setattr(ingest_cli, "ingest", lambda url, strategy="ast": _result())
    dump = tmp_path / "chunks.jsonl"

    ingest_cli.main(["https://github.com/owner/repo", "--json", "--dump", str(dump)])

    payload = json.loads(capsys.readouterr().out)
    assert payload["dump_path"] == str(dump)
    assert len(dump.read_text(encoding="utf-8").strip().splitlines()) == 2


def test_agent_json_failure_is_a_json_envelope(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert agent_cli.fail("AgentError", "GOOGLE_API_KEY is required", as_json=True) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"] == {
        "type": "AgentError",
        "message": "GOOGLE_API_KEY is required",
    }


def test_agent_failure_without_json_stays_prose_on_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert agent_cli.fail("AgentError", "no key", as_json=False) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() == "error: no key"
