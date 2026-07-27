"""Fixed-size character-window chunking — the Phase 6 baseline only (SPEC §2.7).

This is a **deliberate, narrowly-scoped exception to CLAUDE.md hard rule 4**
("chunk boundaries come from tree-sitter AST nodes; never raw character
splits"). It exists so the README can state what rule 4 actually buys, measured
rather than asserted. See DECISIONS 2026-07-27 "naive-chunking baseline".

Scope of the exception, enforced by the call sites:

* reachable only through ``python -m app.ingest.cli --strategy naive``
* the API, the ARQ worker, and every default path stay AST-only
* no tree-sitter, no Jedi, no symbol awareness — this module imports none of it

The header keeps the §2.4 *shape* so the two corpora differ in boundary logic
and nothing else structurally, but its symbol-derived fields are honestly empty:
a fixed-window splitter cannot know what symbol it landed in. That difference is
part of what is being measured, and it is recorded in DECISIONS rather than
papered over.
"""

from __future__ import annotations

from app.config import NAIVE_CHUNK_CHARS, NAIVE_CHUNK_OVERLAP_CHARS
from app.ingest.chunker import Chunk
from app.ingest.filters import SourceFile


def _window_bounds(n_chars: int) -> list[tuple[int, int]]:
    """Return ``(start, end)`` character offsets for one file's windows.

    Windows advance by ``NAIVE_CHUNK_CHARS - NAIVE_CHUNK_OVERLAP_CHARS`` so
    consecutive windows overlap, which is the whole point of the technique: a
    construct straddling a boundary survives in at least one window.
    """
    if n_chars <= 0:
        return []
    stride = max(NAIVE_CHUNK_CHARS - NAIVE_CHUNK_OVERLAP_CHARS, 1)
    bounds: list[tuple[int, int]] = []
    start = 0
    while start < n_chars:
        end = min(start + NAIVE_CHUNK_CHARS, n_chars)
        bounds.append((start, end))
        if end == n_chars:
            break
        start += stride
    return bounds


def _line_index(text: str) -> list[int]:
    """Character offset at which each 1-based line starts (index 0 unused)."""
    offsets = [0, 0]
    for i, ch in enumerate(text):
        if ch == "\n":
            offsets.append(i + 1)
    return offsets


def _line_of(offsets: list[int], pos: int) -> int:
    """1-based line number containing character offset ``pos``."""
    lo, hi = 1, len(offsets) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if offsets[mid] <= pos:
            lo = mid
        else:
            hi = mid - 1
    return lo


def _header(file_path: str, part: int, n_parts: int) -> str:
    """The §2.4 header shape, with symbol-derived fields left empty.

    ``Symbol``/``Kind``/``Imports`` are AST products. Filling them here would
    hand the baseline the enrichment that AST chunking is supposed to be judged
    on, so they stay blank and the omission is stated in DECISIONS.
    """
    return "\n".join(
        [
            f"# File: {file_path}",
            f"# Symbol: {file_path}:w{part}",
            "# Kind: window",
            "# Imports:",
            f"# Part: {part}/{n_parts}",
        ]
    )


def naive_chunk_file(source: SourceFile) -> list[Chunk]:
    """Split one file into fixed-size overlapping character windows.

    Every chunk still carries ``file_path``/``start_line``/``end_line`` — hard
    rule 5 is not part of the carve-out, and the baseline has to be able to cite
    or the answer-level comparison would be meaningless.
    """
    text = source.text
    bounds = _window_bounds(len(text))
    if not bounds:
        return []

    offsets = _line_index(text)
    n_parts = len(bounds)
    chunks: list[Chunk] = []
    for i, (start, end) in enumerate(bounds, start=1):
        # end - 1: the window's last character, so a window ending exactly on a
        # newline does not claim the following line.
        chunks.append(
            Chunk(
                file_path=source.path,
                symbol=f"{source.path}:w{i}",
                kind="window",
                part=i,
                n_parts=n_parts,
                start_line=_line_of(offsets, start),
                end_line=_line_of(offsets, max(end - 1, start)),
                header=_header(source.path, i, n_parts),
                code=text[start:end],
            )
        )
    return chunks
