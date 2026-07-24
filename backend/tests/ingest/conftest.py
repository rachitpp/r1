"""Shared fixtures for ingestion tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from git import Repo

from app.ingest.parser import ParsedFile, parse_file
from app.ingest.filters import SourceFile


@pytest.fixture
def make_repo(tmp_path: Path):
    """Return a factory that builds a git repo with the given files.

    Files are written and ``git add``-ed (staged) so ``git ls-files`` sees
    them — no commit needed. Values may be ``str`` (utf-8) or ``bytes``.
    """

    def _make(files: dict[str, str | bytes]) -> Path:
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        repo = Repo.init(repo_dir)
        for rel, content in files.items():
            dest = repo_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                dest.write_bytes(content)
            else:
                # newline="" preserves LF exactly (no platform CRLF rewrite),
                # keeping fixtures deterministic across OSes.
                dest.write_text(content, encoding="utf-8", newline="")
        repo.git.add(A=True)
        return repo_dir

    return _make


def parse_source(path: str, text: str) -> ParsedFile:
    """Parse an inline source string; asserts the parse succeeded."""
    parsed = parse_file(SourceFile(path=path, text=text, n_lines=text.count("\n")))
    assert parsed is not None
    return parsed
