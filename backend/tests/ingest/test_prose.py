"""Prose and config chunking (SPEC §30.2, §30.3).

The two things worth holding: selection puts the right files in the right class,
and the chunker splits on headings rather than on characters — rule 4's actual
requirement, met by a line-scanner instead of a grammar.
"""

from __future__ import annotations

import pytest

from app.ingest.filters import SourceFile, classify_path
from app.ingest.prose import chunk_prose_file, split_sections
from app.ingest.token_budget import HeuristicTokenCounter


@pytest.fixture
def counter() -> HeuristicTokenCounter:
    return HeuristicTokenCounter()


def _source(path: str, text: str, cls: str = "document") -> SourceFile:
    return SourceFile(
        path=path,
        text=text,
        n_lines=text.count("\n") + 1,
        file_class=cls,  # type: ignore[arg-type]
    )


# --- §30.2 selection --------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("app/main.py", "code"),
        # §30.2's footnote: these are `*.py` and are NOT reclassified.
        ("setup.py", "code"),
        ("noxfile.py", "code"),
        ("README.md", "document"),
        ("docs/guide.rst", "document"),
        ("NOTES.txt", "document"),
        ("pyproject.toml", "config"),
        ("setup.cfg", "config"),
        ("requirements.txt", "config"),
        ("requirements-dev.txt", "config"),
        ("Dockerfile", "config"),
        ("Dockerfile.alpine", "config"),
        ("docker-compose.yml", "config"),
        ("Makefile", "config"),
        ("tox.ini", "config"),
        (".github/workflows/ci.yml", "config"),
        # Not selected at all.
        ("logo.svg", None),
        ("data.csv", None),
        ("app/module.pyc", None),
        # A yaml outside the workflows dir is not CI config.
        ("config/values.yml", None),
    ],
)
def test_classification(path: str, expected: str | None) -> None:
    assert classify_path(path) == expected


def test_requirements_is_config_not_a_text_document() -> None:
    """`.txt` would make it prose; it has no headings, so config wins first."""
    assert classify_path("requirements.txt") == "config"


# --- §30.3 heading chunking -------------------------------------------------


def test_markdown_splits_on_headings_and_keeps_the_path() -> None:
    text = "\n".join(
        [
            "Intro paragraph.",
            "",
            "# Quickstart",
            "Some words.",
            "",
            "## Installation",
            "    pip install httpx",
            "",
            "## Usage",
            "Call it.",
        ]
    )
    sections = split_sections(text)
    paths = [" > ".join(s.path) for s in sections]

    assert paths == [
        "",  # the preamble keeps its content rather than being dropped
        "Quickstart",
        "Quickstart > Installation",
        "Quickstart > Usage",
    ]


def test_a_heading_inside_a_fenced_block_is_content_not_structure() -> None:
    """A README showing `# Install` in a shell block must not open a section."""
    text = "\n".join(
        [
            "# Real Heading",
            "```bash",
            "# Install",
            "pip install x",
            "```",
            "After.",
        ]
    )
    assert [" > ".join(s.path) for s in split_sections(text)] == ["Real Heading"]


def test_rst_over_underlined_titles_are_headings() -> None:
    text = "\n".join(
        [
            "Welcome",
            "=======",
            "Body text.",
            "",
            "Install",
            "-------",
            "More text.",
        ]
    )
    assert [" > ".join(s.path) for s in split_sections(text)] == [
        "Welcome",
        "Welcome > Install",
    ]


def test_an_underline_shorter_than_its_title_is_not_a_heading() -> None:
    """Short punctuation runs are table rules and horizontal rules, not titles."""
    # 17-character title, 3-character rule underneath it.
    assert [" > ".join(s.path) for s in split_sections("A long title here\n---\nbody")] == [""]
    # Same length as the title: a real rST underline.
    assert [" > ".join(s.path) for s in split_sections("Title\n-----\nbody")] == ["Title"]


def test_chunk_carries_the_section_header_and_a_null_symbol(
    counter: HeuristicTokenCounter,
) -> None:
    src = _source("README.md", "# Quickstart\nInstall with pip.\n")
    (chunk,) = chunk_prose_file(src, counter)

    assert chunk.kind == "document"
    # NULL, not the heading path: `symbol` is a qualname the graph joins on.
    assert chunk.symbol is None
    assert "# File: README.md" in chunk.header
    assert "# Section: Quickstart" in chunk.header
    assert "# Kind: document" in chunk.header
    assert chunk.text.startswith(chunk.header + "\n---\n")


def test_line_numbers_point_at_the_real_file_lines(
    counter: HeuristicTokenCounter,
) -> None:
    """Citations are line-anchored (hard rule 5), including for prose."""
    src = _source("README.md", "Intro.\n\n# Setup\nRun it.\n\n# Later\nMore.\n")
    chunks = chunk_prose_file(src, counter)

    setup = next(c for c in chunks if "# Section: Setup" in c.header)
    assert setup.start_line == 3
    assert setup.end_line >= setup.start_line


def test_config_is_never_split_on_headings(counter: HeuristicTokenCounter) -> None:
    """A `pyproject.toml` cut in half is a `pyproject.toml` that answers nothing."""
    text = "[project]\nname = 'x'\n\n# a comment that looks like a heading\ndeps = []\n"
    src = _source("pyproject.toml", text, cls="config")
    chunks = chunk_prose_file(src, counter)

    assert len(chunks) == 1
    assert chunks[0].kind == "config"
    assert chunks[0].code == text
    assert "# Section:" not in chunks[0].header


def test_oversize_content_falls_back_to_line_splitting(
    counter: HeuristicTokenCounter,
) -> None:
    """§2.5 part-splitting, on line boundaries — never mid-line."""
    body = "\n".join(f"line {i} with several words in it" for i in range(600))
    src = _source("BIG.md", f"# Huge\n{body}\n")
    chunks = chunk_prose_file(src, counter)

    assert len(chunks) > 1
    assert all(c.n_parts == len(chunks) for c in chunks)
    assert [c.part for c in chunks] == list(range(1, len(chunks) + 1))
    # Every part is line-aligned: reassembling loses nothing but the joins.
    rejoined = "\n".join(c.code for c in chunks)
    assert rejoined.count("line 599") == 1


def test_a_file_of_only_blank_lines_yields_nothing(
    counter: HeuristicTokenCounter,
) -> None:
    assert chunk_prose_file(_source("EMPTY.md", "\n\n\n"), counter) == []
