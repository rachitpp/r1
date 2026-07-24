"""Chunker tests: header format and oversize statement-boundary splitting."""

from __future__ import annotations

from app.config import CHUNK_TOKEN_MAX
from app.ingest.chunker import SEPARATOR, chunk_file
from app.ingest.tokens import HeuristicTokenCounter

from .conftest import parse_source

COUNTER = HeuristicTokenCounter()


def _chunks(path: str, text: str):
    return chunk_file(parse_source(path, text), COUNTER)


def test_header_format_for_method() -> None:
    src = (
        "import jwt\n"
        "from .models import User\n"
        "\n"
        "class Auth:\n"
        "    def verify(self, token: str) -> User | None:\n"
        "        return None\n"
    )
    chunk = next(c for c in _chunks("src/auth.py", src) if c.symbol.endswith("verify"))
    assert chunk.header == (
        "# File: src/auth.py\n"
        "# Symbol: src.auth.Auth.verify\n"
        "# Kind: method\n"
        "# Signature: def verify(self, token: str) -> User | None\n"
        "# Imports: import jwt; from .models import User"
    )
    assert chunk.text == chunk.header + SEPARATOR + chunk.code
    assert chunk.part == 1 and chunk.n_parts == 1


def test_module_chunk_has_no_signature_line() -> None:
    chunk = next(c for c in _chunks("m.py", "import os\nX = 1\n") if c.kind == "module")
    assert "# Signature:" not in chunk.header
    assert "# Kind: module" in chunk.header


def _oversized_function() -> str:
    body = "\n".join(
        f"    v{i} = transform(alpha, beta, gamma, delta, epsilon, zeta)"
        for i in range(60)
    )
    return f"import mod\n\ndef big(alpha, beta, gamma, delta):\n{body}\n    return v0\n"


def test_oversize_splits_on_statement_boundaries() -> None:
    chunks = _chunks("big.py", _oversized_function())
    parts = [c for c in chunks if c.kind == "function"]
    assert len(parts) > 1
    # Every part is within (or, for an unsplittable single statement, reported
    # as) the token budget; here each part fits.
    assert all(COUNTER.token_len(c.text) <= CHUNK_TOKEN_MAX for c in parts)
    # Part headers present and numbered 1..n.
    assert [c.part for c in parts] == list(range(1, len(parts) + 1))
    assert all(c.n_parts == len(parts) for c in parts)
    assert all(f"# Part: {c.part}/{c.n_parts}" in c.header for c in parts)


def test_oversize_parts_have_contiguous_line_ranges() -> None:
    parts = [c for c in _chunks("big.py", _oversized_function()) if c.kind == "function"]
    assert parts[0].start_line == 3  # the def line
    for a, b in zip(parts, parts[1:]):
        assert b.start_line == a.end_line + 1


def test_oversize_first_part_keeps_def_line() -> None:
    parts = [c for c in _chunks("big.py", _oversized_function()) if c.kind == "function"]
    assert parts[0].code.splitlines()[0] == "def big(alpha, beta, gamma, delta):"
    # No statement is split mid-line: every body line is a complete assignment.
    for line in parts[1].code.splitlines():
        assert line.strip().startswith("v") or line.strip() == "return v0"
