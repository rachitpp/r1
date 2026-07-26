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
from typing import TypedDict
from uuid import UUID

import asyncpg

# [path:start-end] — path may contain dots, slashes, dashes, underscores.
CITATION_RE = re.compile(r"\[([\w./\-]+\.py):(\d+)-(\d+)\]")


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
    conn: asyncpg.Connection, repo_id: UUID, citations: list[Citation]
) -> list[Citation]:
    """Drop citations whose file is not in the repo; clamp ranges to EOF."""
    if not citations:
        return []
    rows = await conn.fetch(
        "SELECT path, n_lines FROM files WHERE repo_id = $1 AND path = ANY($2::text[])",
        repo_id,
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
