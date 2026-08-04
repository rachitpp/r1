"""Chunk prose and configuration files (SPEC §30.3).

Rule 4 requires structural boundaries, never raw character splits. For prose the
structural boundary is the **heading** — markdown ``#``-``######`` and rST
over/underlined titles — so this emits one chunk per section, carrying the
heading path that led to it.

**A line-scanner, not a grammar.** ``tree-sitter-markdown`` would be the literal
reading of rule 4 and is a new dependency (rule 11) for a boundary that is
unambiguous in the first character of a line. Rule 4's purpose is that
boundaries carry meaning; a heading satisfies that. Recorded rather than
assumed — DECISIONS 2026-08-04.

Configuration files are **not** split at all. They are small, and a
``pyproject.toml`` cut in half is a ``pyproject.toml`` that answers nothing.
Oversize files of either class fall to §2.5 part-splitting on line boundaries,
which is the one place a prose chunk can be cut somewhere its author did not
choose.
"""

from __future__ import annotations

import re

from app.config import CHUNK_TOKEN_MAX
from app.ingest.chunker import SEPARATOR, Chunk
from app.ingest.filters import SourceFile
from app.ingest.token_budget import TokenCounter

# `#` through `######`, then at least one space, then the title. The space is
# required by CommonMark and is what keeps a `#!/usr/bin/env` line, or a Python
# comment in a fenced block, from being read as a heading.
_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")

# rST underlines: a run of one punctuation character, at least as long as the
# title above it. The set and its order are from the reST spec's suggested
# hierarchy; any of them is accepted at any level, because a real document picks
# its own order and only has to be internally consistent.
_RST_UNDERLINE = re.compile(r"^([=\-`:.'\"~^_*+#])\1{1,}\s*$")

# Fenced code blocks. Headings inside one are content, not structure — a README
# showing `# Install` inside a shell block must not open a section.
_FENCE = re.compile(r"^\s*(```|~~~)")

# A rST title needs a real line above/below it, and a line that is itself all
# punctuation is an underline rather than a title.
_MIN_RST_TITLE_LEN = 1


def _is_rst_title(lines: list[str], i: int) -> bool:
    """Whether ``lines[i]`` is an rST title underlined by ``lines[i + 1]``."""
    if i + 1 >= len(lines):
        return False
    title, underline = lines[i].rstrip(), lines[i + 1].rstrip()
    if len(title) < _MIN_RST_TITLE_LEN or not title.strip():
        return False
    if _RST_UNDERLINE.match(title):
        return False  # an over-line, handled as part of the pair below it
    match = _RST_UNDERLINE.match(underline)
    if match is None:
        return False
    # The underline must cover the title. Shorter runs appear in ASCII tables
    # and horizontal rules, which are not headings.
    return len(underline) >= len(title)


class _Section:
    """A heading and the body lines beneath it, with 1-based file line numbers."""

    def __init__(self, path: list[str], start_line: int) -> None:
        self.path = path
        self.start_line = start_line
        self.lines: list[str] = []

    @property
    def end_line(self) -> int:
        # A section with no body still spans its own heading line.
        return self.start_line + max(len(self.lines) - 1, 0)


def split_sections(text: str) -> list[_Section]:
    """Split markdown/rST ``text`` into heading-delimited sections.

    Content before the first heading becomes a leading section with an empty
    heading path — a README's opening paragraph is often the most useful thing
    in it, and dropping it for want of a heading would be a strange trade.
    """
    lines = text.splitlines()
    sections: list[_Section] = []
    current = _Section(path=[], start_line=1)
    # Heading path by level, so `## Usage` under `# Guide` yields "Guide > Usage".
    stack: list[tuple[int, str]] = []
    in_fence = False
    skip_next = False

    for i, line in enumerate(lines):
        if skip_next:
            skip_next = False
            current.lines.append(line)
            continue

        if _FENCE.match(line):
            in_fence = not in_fence
            current.lines.append(line)
            continue
        if in_fence:
            current.lines.append(line)
            continue

        level: int | None = None
        title = ""
        md = _MD_HEADING.match(line)
        if md is not None:
            level, title = len(md.group(1)), md.group(2).strip()
        elif _is_rst_title(lines, i):
            # rST has no intrinsic level per character, so nesting is by order of
            # first appearance. Depth is the stack's depth at the time, which is
            # what a reader sees rather than what the spec permits.
            level, title = len(stack) + 1, lines[i].strip()
            skip_next = True  # the underline belongs to the heading, not the body

        if level is None:
            current.lines.append(line)
            continue

        if current.lines or current.path:
            sections.append(current)
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        current = _Section(path=[t for _, t in stack], start_line=i + 1)
        current.lines.append(line)

    if current.lines or current.path:
        sections.append(current)
    return sections


def _header(file_path: str, kind: str, section: str | None, part: int, n: int) -> str:
    lines = [f"# File: {file_path}"]
    if section:
        lines.append(f"# Section: {section}")
    lines.append(f"# Kind: {kind}")
    if n > 1:
        lines.append(f"# Part: {part}/{n}")
    return "\n".join(lines)


def _split_lines_to_budget(
    body: str, header_len: int, counter: TokenCounter
) -> list[tuple[str, int, int]]:
    """Greedily pack ``body`` lines into parts under the token budget.

    Returns ``(text, start_offset, end_offset)`` with 0-based line offsets into
    ``body``. §2.5 part-splitting, on line boundaries — the only cut in §30 that
    lands somewhere the document's author did not choose.
    """
    budget = max(CHUNK_TOKEN_MAX - header_len, 1)
    lines = body.splitlines()
    if not lines:
        return [(body, 0, 0)]

    parts: list[tuple[str, int, int]] = []
    cur: list[str] = []
    start = 0
    for idx, line in enumerate(lines):
        tentative = "\n".join([*cur, line])
        if cur and counter.token_len(tentative) > budget:
            parts.append(("\n".join(cur), start, idx - 1))
            cur = [line]
            start = idx
        else:
            cur.append(line)
    if cur:
        parts.append(("\n".join(cur), start, len(lines) - 1))
    return parts


def _emit(
    file_path: str,
    kind: str,
    section: str | None,
    body: str,
    first_line: int,
    counter: TokenCounter,
) -> list[Chunk]:
    """One section (or a whole config file) as one or more chunks."""
    single = _header(file_path, kind, section, 1, 1)
    if counter.token_len(single + SEPARATOR + body) <= CHUNK_TOKEN_MAX:
        n_lines = max(len(body.splitlines()), 1)
        return [
            Chunk(
                file_path=file_path,
                # NULL, not the heading path: `symbol` is a qualname the symbol
                # graph joins on, and a heading resolves to nothing (§30.3).
                symbol=None,
                kind=kind,
                part=1,
                n_parts=1,
                start_line=first_line,
                end_line=first_line + n_lines - 1,
                header=single,
                code=body,
            )
        ]

    est = _header(file_path, kind, section, 1, 2)
    packed = _split_lines_to_budget(
        body, counter.token_len(est + SEPARATOR), counter
    )
    n = len(packed)
    return [
        Chunk(
            file_path=file_path,
            symbol=None,
            kind=kind,
            part=i,
            n_parts=n,
            start_line=first_line + s,
            end_line=first_line + e,
            header=_header(file_path, kind, section, i, n),
            code=text,
        )
        for i, (text, s, e) in enumerate(packed, start=1)
    ]


def chunk_prose_file(source: SourceFile, counter: TokenCounter) -> list[Chunk]:
    """Chunk one selected prose/config file (SPEC §30.3).

    ``document`` splits on headings; ``config`` is emitted whole. Both keep the
    §2.4 chunk shape, ``header + "\\n---\\n" + body``, so nothing downstream —
    embedding, storage, retrieval, citation — needs to know which path produced
    a chunk.
    """
    kind = source.file_class
    if kind == "config":
        return _emit(source.path, kind, None, source.text, 1, counter)

    out: list[Chunk] = []
    for section in split_sections(source.text):
        body = "\n".join(section.lines)
        if not body.strip():
            continue  # a heading-only run of blank lines carries no evidence
        out.extend(
            _emit(
                source.path,
                kind,
                " > ".join(section.path) if section.path else None,
                body,
                section.start_line,
                counter,
            )
        )
    return out
