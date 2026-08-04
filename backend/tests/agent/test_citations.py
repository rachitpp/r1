"""Citation parsing and validation (SPEC §7.5)."""

from __future__ import annotations

import uuid

import pytest

from app.agent.citations import parse_citations, validate_citations

REPO_ID = uuid.uuid4()


def test_parses_inline_citations_in_order() -> None:
    text = "Auth starts in [httpx/_auth.py:1-20] then [httpx/_client.py:5-9]."
    assert parse_citations(text) == [
        {"file_path": "httpx/_auth.py", "start_line": 1, "end_line": 20},
        {"file_path": "httpx/_client.py", "start_line": 5, "end_line": 9},
    ]


def test_dedupes_repeated_citations() -> None:
    text = "[a/b.py:1-2] and again [a/b.py:1-2]"
    assert len(parse_citations(text)) == 1


@pytest.mark.parametrize(
    "path",
    [
        "httpx/_client.py",
        "README.md",
        "docs/index.rst",
        "NOTES.txt",
        "pyproject.toml",
        "setup.cfg",
        "tox.ini",
        ".github/workflows/test-suite.yml",
        "docker-compose.yaml",
        "Dockerfile",
        "Dockerfile.alpine",
        "Makefile",
        "requirements-dev.txt",
    ],
)
def test_every_ingestible_class_is_citable(path: str) -> None:
    """§30 put prose in the corpus; §7.5 must be able to cite it.

    This was a real defect: the pattern anchored on `\\.py`, so an overview that
    correctly wrote `[README.md:90-104]` had the citation dropped by validation
    and rendered the marker as literal text.
    """
    assert parse_citations(f"see [{path}:2-9] for detail") == [
        {"file_path": path, "start_line": 2, "end_line": 9}
    ]


@pytest.mark.parametrize(
    "text",
    [
        "as noted [see 1-2] elsewhere",
        "in [RFC 2616:1-5]",
        "a range [1-2] alone",
    ],
)
def test_ordinary_prose_is_not_mistaken_for_a_citation(text: str) -> None:
    """Widening the path pattern must not turn brackets in prose into citations."""
    assert parse_citations(text) == []


def test_ignores_malformed_ranges() -> None:
    assert parse_citations("[a.py:0-5]") == []      # 1-based lines only
    assert parse_citations("[a.py:9-3]") == []      # end before start
    assert parse_citations("[a.py:1]") == []        # no range
    assert parse_citations("no citations here") == []


def test_ignores_paths_of_classes_the_corpus_never_holds() -> None:
    """Still a closed set — widened by §30, not opened.

    `README.md` used to belong in this test and now does not: §30 puts it in the
    corpus, so citing it is correct. What stays excluded is anything selection
    never stores, because a citation of it could only be a fabrication.
    """
    assert parse_citations("[logo.svg:1-2]") == []
    assert parse_citations("[data.csv:1-2]") == []
    assert parse_citations("[notes.pdf:1-2]") == []


class _Conn:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    async def fetch(self, *_a: object, **_k: object) -> list[dict]:
        return self.rows


@pytest.mark.asyncio
async def test_validation_drops_fabricated_paths() -> None:
    """A path not in the repo is a fabrication — dropped, not clamped."""
    conn = _Conn([{"path": "real.py", "n_lines": 100}])
    out = await validate_citations(
        conn,  # type: ignore[arg-type]
        REPO_ID,
        [
            {"file_path": "real.py", "start_line": 1, "end_line": 5},
            {"file_path": "invented.py", "start_line": 1, "end_line": 5},
        ],
    )
    assert [c["file_path"] for c in out] == ["real.py"]


@pytest.mark.asyncio
async def test_validation_clamps_overshooting_ranges() -> None:
    """Right file, overshot span: keep it, clamped to EOF."""
    conn = _Conn([{"path": "real.py", "n_lines": 10}])
    out = await validate_citations(
        conn,  # type: ignore[arg-type]
        REPO_ID,
        [{"file_path": "real.py", "start_line": 5, "end_line": 999}],
    )
    assert out == [{"file_path": "real.py", "start_line": 5, "end_line": 10}]
