"""Naive-baseline chunker tests (SPEC §2.7).

The baseline only has to be *honest*, not good: fixed windows, real overlap,
and citations that point at the lines the code actually came from. The last
property is the one that matters — hard rule 5 is not part of the rule-4
carve-out, and a baseline that cannot cite cannot be compared at answer level.
"""

from __future__ import annotations

from app.config import NAIVE_CHUNK_CHARS, NAIVE_CHUNK_OVERLAP_CHARS
from app.ingest.filters import SourceFile
from app.ingest.naive import naive_chunk_file


def _source(text: str, path: str = "pkg/mod.py") -> SourceFile:
    return SourceFile(path=path, text=text, n_lines=text.count("\n") + 1)


def test_short_file_is_one_window() -> None:
    chunks = naive_chunk_file(_source("x = 1\ny = 2\n"))
    assert len(chunks) == 1
    assert chunks[0].code == "x = 1\ny = 2\n"
    assert chunks[0].part == 1
    assert chunks[0].n_parts == 1


def test_empty_file_yields_nothing() -> None:
    assert naive_chunk_file(_source("")) == []


def test_windows_are_capped_and_overlap() -> None:
    text = "\n".join(f"line_{i} = {i}" for i in range(600))
    chunks = naive_chunk_file(_source(text))

    assert len(chunks) > 1
    assert all(len(c.code) <= NAIVE_CHUNK_CHARS for c in chunks)
    # Consecutive windows share exactly the overlap, which is the point of the
    # technique: a construct on a boundary survives in at least one window.
    first, second = chunks[0], chunks[1]
    assert first.code[-NAIVE_CHUNK_OVERLAP_CHARS:] == (
        second.code[:NAIVE_CHUNK_OVERLAP_CHARS]
    )


def test_windows_cover_the_whole_file() -> None:
    text = "\n".join(f"line_{i} = {i}" for i in range(600))
    chunks = naive_chunk_file(_source(text))

    stride = NAIVE_CHUNK_CHARS - NAIVE_CHUNK_OVERLAP_CHARS
    rebuilt = "".join(c.code[:stride] for c in chunks[:-1]) + chunks[-1].code
    assert rebuilt == text


def test_line_numbers_locate_the_code() -> None:
    """start_line/end_line must bracket the window's real source lines."""
    text = "\n".join(f"line_{i} = {i}" for i in range(600))
    lines = text.split("\n")
    for chunk in naive_chunk_file(_source(text)):
        assert 1 <= chunk.start_line <= chunk.end_line <= len(lines)
        # The window's first non-empty line really is at start_line.
        first_line = chunk.code.split("\n")[0]
        if first_line:
            assert first_line in lines[chunk.start_line - 1]


def test_header_keeps_the_shape_without_faking_symbols() -> None:
    chunk = naive_chunk_file(_source("x = 1\n"))[0]
    assert chunk.header == (
        "# File: pkg/mod.py\n"
        "# Symbol: pkg/mod.py:w1\n"
        "# Kind: window\n"
        "# Imports:\n"
        "# Part: 1/1"
    )
    assert chunk.kind == "window"


def test_parts_are_numbered_over_the_file() -> None:
    text = "z = 0\n" * 800
    chunks = naive_chunk_file(_source(text))
    assert [c.part for c in chunks] == list(range(1, len(chunks) + 1))
    assert {c.n_parts for c in chunks} == {len(chunks)}
