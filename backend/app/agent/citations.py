"""Citation parsing and validation (SPEC §7.5).

The contract is ``[path:start-end]`` inline in the answer. Parsing is shared
with Phase 4's SSE layer, so it lives here rather than inside the graph.

Validation is deliberately strict about *existence* and lenient about *range*:
a citation naming a file that isn't in the repo is a fabrication and gets
dropped, while one whose end line runs past EOF is clamped — the model found
the right file and overshot the span, which is worth keeping.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import TypedDict
from uuid import UUID

import asyncpg

from app.config import (
    CI_WORKFLOW_EXTENSIONS,
    CONFIG_EXTENSIONS,
    CONFIG_FILENAMES,
    PROSE_EXTENSIONS,
)

# Every extension a chunk can come from: `.py`, plus everything §30.2 selects.
#
# **Derived from the §12 constants, not restated.** This pattern used to be
# `\.py` alone, which silently dropped every citation of a README the moment §30
# put one in the corpus — the answer still *said* `[README.md:90-104]` and the
# validated list came back without it, so the claim rendered as literal text
# instead of a chip. Widening selection must widen citations in one edit, or the
# next class of chunk repeats it.
_CITABLE_SUFFIXES = {
    ".py",
    *PROSE_EXTENSIONS,
    *CONFIG_EXTENSIONS,
    *CI_WORKFLOW_EXTENSIONS,
    *{s for s in (PurePosixPath(n).suffix for n in CONFIG_FILENAMES) if s},
}
# Config files with no extension at all. `Dockerfile.alpine` is covered by the
# trailing `[\w.\-]*`, which is also why these are matched as a prefix.
_BARE_NAMES = ("Dockerfile", "Makefile", "Pipfile")


def _alt(items: object) -> str:
    # Longest-first so `.yaml` cannot be shadowed by a `.yml` prefix match.
    return "|".join(re.escape(s) for s in sorted(items, key=len, reverse=True))  # type: ignore[call-overload]


# [path:start-end] — path may contain dots, slashes, dashes, underscores.
# A fabricated path still gets dropped by `validate_citations`; this pattern
# only has to avoid matching ordinary prose like `[see 1-2]`.
CITATION_RE = re.compile(
    r"\[((?:[\w./\-]*(?:" + _alt(_CITABLE_SUFFIXES) + r"))"
    r"|(?:[\w./\-]*(?:" + _alt(_BARE_NAMES) + r")[\w.\-]*)"
    r"):(\d+)-(\d+)\]"
)


class Citation(TypedDict):
    file_path: str
    start_line: int
    end_line: int


def parse_citations(text: str) -> list[Citation]:
    """Extract ``[path:start-end]`` citations, deduped, in order of appearance."""
    out: list[Citation] = []
    seen: set[tuple[str, int, int]] = set()
    for m in CITATION_RE.finditer(text):
        path, start, end = m.group(1), int(m.group(2)), int(m.group(3))
        if start < 1 or end < start:
            continue  # malformed range — not a citation
        key = (path, start, end)
        if key in seen:
            continue
        seen.add(key)
        out.append({"file_path": path, "start_line": start, "end_line": end})
    return out


async def validate_citations(
    conn: asyncpg.Connection, snapshot_id: UUID, citations: list[Citation]
) -> list[Citation]:
    """Drop citations whose file is not in the repo; clamp ranges to EOF."""
    if not citations:
        return []
    rows = await conn.fetch(
        "SELECT path, n_lines FROM files WHERE snapshot_id = $1 AND path = ANY($2::text[])",
        snapshot_id,
        [c["file_path"] for c in citations],
    )
    n_lines_of = {str(r["path"]): int(r["n_lines"]) for r in rows}
    out: list[Citation] = []
    for c in citations:
        n = n_lines_of.get(c["file_path"])
        if n is None:
            continue  # fabricated path
        out.append(
            {
                "file_path": c["file_path"],
                "start_line": min(c["start_line"], n),
                "end_line": min(c["end_line"], n),
            }
        )
    return out
