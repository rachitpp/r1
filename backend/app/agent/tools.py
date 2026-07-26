"""The six agent tools (SPEC §7.1), exact signatures.

Every tool returns a JSON-serializable dict and **never raises into the loop**:
failures come back as ``{"error": "..."}`` so the model reads the problem and
adapts, rather than the graph dying mid-run. A tool that raises is a bug.

``search_code`` is the only retrieval entry point and uses
:func:`retrieval.hybrid_search` with its **default** configuration — RRF fusion
over implementation chunks, rerank off (§5.3), tests excluded (§5.4). It must
not pass ``rerank=True``: the cross-encoder measured worse-or-equal to plain
fusion at every k and at MRR (DECISIONS 2026-07-26).
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Any
from uuid import UUID

import asyncpg

from app.agent.context import called_by_block
from app.config import (
    EXPAND_MAX_DEPTH,
    EXPAND_TOKEN_BUDGET,
    READ_MAX_LINES,
    SEARCH_K,
)
from app.retrieval.hybrid import search

logger = logging.getLogger(__name__)

MAX_DEFINITION_MATCHES = 5
EDGE_KINDS = ("imports", "calls", "extends")
DIRECTIONS = ("out", "in", "both")


def _err(message: str) -> dict[str, str]:
    return {"error": message}


def _number_lines(code: str, start_line: int) -> str:
    """Prefix each line with its absolute file line number.

    Citations are line-anchored (§7.5), so the model needs real file lines in
    front of it — not offsets into a snippet it can't map back.
    """
    return "\n".join(
        f"{start_line + i:>6}  {line}" for i, line in enumerate(code.splitlines())
    )


# ---------------------------------------------------------------------------
# search_code
# ---------------------------------------------------------------------------


async def search_code(
    conn: asyncpg.Connection, repo_id: UUID, query: str, k: int = SEARCH_K
) -> dict[str, Any]:
    """Semantic entry-point finding over implementation chunks (§5.4)."""
    if not query.strip():
        return _err("query must be a non-empty string")
    try:
        hits = await search(conn, repo_id, query, k=k, mode="hybrid")
    except Exception as exc:  # noqa: BLE001 — never raise into the loop
        logger.exception("search_code failed")
        return _err(f"search failed: {exc}")
    return {"hits": hits}


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------


async def read_file(
    conn: asyncpg.Connection,
    repo_id: UUID,
    path: str,
    start_line: int | None = None,
    end_line: int | None = None,
) -> dict[str, Any]:
    """Line-numbered file content, whole-file only under ``READ_MAX_LINES``."""
    row = await conn.fetchrow(
        "SELECT content, n_lines FROM files WHERE repo_id = $1 AND path = $2",
        repo_id,
        path,
    )
    if row is None:
        return _err(f"no such file: {path!r} (use list_directory to see the tree)")

    lines = str(row["content"]).splitlines()
    n_lines = len(lines)

    if start_line is None and end_line is None:
        if n_lines > READ_MAX_LINES:
            return _err(
                f"{path} has {n_lines} lines, over the {READ_MAX_LINES}-line "
                f"whole-file limit; call again with start_line and end_line"
            )
        start, end = 1, n_lines
    else:
        start = max(1, start_line or 1)
        end = min(n_lines, end_line or n_lines)
        if start > end:
            return _err(f"start_line {start} is after end_line {end}")
        if end - start + 1 > READ_MAX_LINES:
            return _err(
                f"requested {end - start + 1} lines, over the "
                f"{READ_MAX_LINES}-line limit; narrow the range"
            )

    body = "\n".join(lines[start - 1 : end])
    return {
        "path": path,
        "start_line": start,
        "end_line": end,
        "content": _number_lines(body, start),
    }


# ---------------------------------------------------------------------------
# get_definition
# ---------------------------------------------------------------------------


async def get_definition(
    conn: asyncpg.Connection,
    repo_id: UUID,
    symbol: str,
    *,
    include_tests: bool = False,
) -> dict[str, Any]:
    """Definitions matching ``symbol`` by name or dotted-qualname suffix.

    This is the exact-identifier lookup that §5.2 injection used to approximate
    — a direct index hit with no retrieval scoring to lose against
    (DECISIONS 2026-07-26). Test-file definitions are excluded by default (§6.3).
    """
    if not symbol.strip():
        return _err("symbol must be a non-empty string")
    rows = await conn.fetch(
        f"""
        SELECT s.qualname, s.kind, s.file_path, s.start_line, s.end_line,
               c.code, c.symbol_id
          FROM symbols s
          LEFT JOIN chunks c
            ON c.repo_id = s.repo_id AND c.symbol_id = s.id AND c.part = 1
         WHERE s.repo_id = $1
           AND (s.name = $2 OR s.qualname = $2 OR s.qualname LIKE '%.' || $2)
           {"" if include_tests else "AND NOT s.is_test"}
         ORDER BY length(s.qualname), s.qualname
         LIMIT $3
        """,
        repo_id,
        symbol,
        MAX_DEFINITION_MATCHES,
    )
    if not rows:
        scope = "" if include_tests else " (tests excluded; pass include_tests=true)"
        return _err(f"no definition found for {symbol!r}{scope}")

    matches = []
    for r in rows:
        code = str(r["code"] or "")
        block = await called_by_block(
            conn,
            repo_id,
            symbol_id=r["symbol_id"],
            file_path=str(r["file_path"]),
            qualname=str(r["qualname"]),
        )
        matches.append(
            {
                "qualname": str(r["qualname"]),
                "kind": str(r["kind"]),
                "file_path": str(r["file_path"]),
                "start_line": int(r["start_line"]),
                "end_line": int(r["end_line"]),
                "code": f"{code}\n{block}" if block else code,
            }
        )
    return {"matches": matches}


# ---------------------------------------------------------------------------
# find_references
# ---------------------------------------------------------------------------


async def find_references(
    conn: asyncpg.Connection,
    repo_id: UUID,
    symbol: str,
    kind: str | None = None,
    *,
    include_tests: bool = False,
) -> dict[str, Any]:
    """Incoming edges to ``symbol`` (§6.2 ``in`` direction).

    From-side test symbols are excluded by default (§6.3): on a well-tested
    repo they outnumber the real call sites and bury the answer.
    """
    if kind is not None and kind not in EDGE_KINDS:
        return _err(f"kind must be one of {list(EDGE_KINDS)}, got {kind!r}")
    rows = await conn.fetch(
        f"""
        SELECT f.qualname AS from_qualname, f.file_path,
               COALESCE(e.line, f.start_line) AS line, e.kind
          FROM edges e
          JOIN symbols t ON t.id = e.to_symbol
          JOIN symbols f ON f.id = e.from_symbol
         WHERE e.repo_id = $1
           AND (t.name = $2 OR t.qualname = $2 OR t.qualname LIKE '%.' || $2)
           {"" if include_tests else "AND NOT f.is_test"}
           {"AND e.kind = $3" if kind else ""}
         ORDER BY f.file_path, line
        """,
        *( (repo_id, symbol, kind) if kind else (repo_id, symbol) ),
    )
    return {
        "references": [
            {
                "from_qualname": str(r["from_qualname"]),
                "file_path": str(r["file_path"]),
                "line": int(r["line"]),
                "kind": str(r["kind"]),
            }
            for r in rows
        ]
    }


# ---------------------------------------------------------------------------
# expand_context
# ---------------------------------------------------------------------------


async def expand_context(
    conn: asyncpg.Connection,
    repo_id: UUID,
    symbol: str,
    depth: int = 1,
    direction: str = "out",
    *,
    include_tests: bool = False,
) -> dict[str, Any]:
    """BFS the symbol graph and return the code it reaches (§6.2, §7.1).

    This is the tool the project's thesis rests on: retrieval finds an entry
    point, this pulls in the dependent code retrieval missed. Depth is clamped
    to ``EXPAND_MAX_DEPTH`` and total returned code to ``EXPAND_TOKEN_BUDGET``,
    truncated **breadth-first** — nearer neighbours are more likely relevant
    than a deep chain, so the budget is spent on them first.
    """
    if direction not in DIRECTIONS:
        return _err(f"direction must be one of {list(DIRECTIONS)}, got {direction!r}")
    depth = max(1, min(depth, EXPAND_MAX_DEPTH))

    root = await conn.fetchrow(
        f"""
        SELECT id, qualname FROM symbols
         WHERE repo_id = $1
           AND (name = $2 OR qualname = $2 OR qualname LIKE '%.' || $2)
           {"" if include_tests else "AND NOT is_test"}
         ORDER BY length(qualname)
         LIMIT 1
        """,
        repo_id,
        symbol,
    )
    if root is None:
        return _err(f"no symbol found for {symbol!r}")

    test_clause = "" if include_tests else "AND NOT o.is_test"
    if direction == "out":
        edge_sql = f"""
            SELECT e.to_symbol AS other, e.kind, s.qualname AS from_q,
                   o.qualname AS to_q
              FROM edges e JOIN symbols s ON s.id = e.from_symbol
                           JOIN symbols o ON o.id = e.to_symbol
             WHERE e.repo_id = $1 AND e.from_symbol = $2 {test_clause}
        """
    elif direction == "in":
        edge_sql = f"""
            SELECT e.from_symbol AS other, e.kind, o.qualname AS from_q,
                   s.qualname AS to_q
              FROM edges e JOIN symbols s ON s.id = e.to_symbol
                           JOIN symbols o ON o.id = e.from_symbol
             WHERE e.repo_id = $1 AND e.to_symbol = $2 {test_clause}
        """
    else:
        edge_sql = f"""
            SELECT CASE WHEN e.from_symbol = $2 THEN e.to_symbol
                        ELSE e.from_symbol END AS other,
                   e.kind,
                   sf.qualname AS from_q, st.qualname AS to_q
              FROM edges e JOIN symbols sf ON sf.id = e.from_symbol
                           JOIN symbols st ON st.id = e.to_symbol
                           JOIN symbols o
                             ON o.id = CASE WHEN e.from_symbol = $2
                                            THEN e.to_symbol ELSE e.from_symbol END
             WHERE e.repo_id = $1
               AND (e.from_symbol = $2 OR e.to_symbol = $2) {test_clause}
        """

    seen_ids: set[int] = {int(root["id"])}
    edges_out: list[dict[str, str]] = []
    frontier: deque[tuple[int, int]] = deque([(int(root["id"]), 0)])
    order: list[int] = []

    while frontier:
        node_id, d = frontier.popleft()
        if d >= depth:
            continue
        for r in await conn.fetch(edge_sql, repo_id, node_id):
            other = int(r["other"])
            edges_out.append(
                {
                    "from": str(r["from_q"]),
                    "to": str(r["to_q"]),
                    "kind": str(r["kind"]),
                }
            )
            if other not in seen_ids:
                seen_ids.add(other)
                order.append(other)
                frontier.append((other, d + 1))

    # Bodies, in BFS order, until the token budget runs out.
    symbols_out: list[dict[str, Any]] = []
    truncated = False
    budget = EXPAND_TOKEN_BUDGET
    for sid in order:
        row = await conn.fetchrow(
            """
            SELECT s.qualname, s.file_path, s.start_line, s.end_line, c.code
              FROM symbols s
              LEFT JOIN chunks c
                ON c.repo_id = s.repo_id AND c.symbol_id = s.id AND c.part = 1
             WHERE s.id = $1
            """,
            sid,
        )
        if row is None:
            continue
        code = str(row["code"] or "")
        cost = len(code) // 4  # same heuristic as tokens.HeuristicTokenCounter
        if cost > budget:
            truncated = True
            break
        budget -= cost
        symbols_out.append(
            {
                "qualname": str(row["qualname"]),
                "file_path": str(row["file_path"]),
                "start_line": int(row["start_line"]),
                "end_line": int(row["end_line"]),
                "code": code,
            }
        )

    result: dict[str, Any] = {"edges": edges_out, "symbols": symbols_out}
    if truncated:
        result["truncated"] = True
    return result


# ---------------------------------------------------------------------------
# list_directory
# ---------------------------------------------------------------------------


async def list_directory(
    conn: asyncpg.Connection, repo_id: UUID, path: str = ""
) -> dict[str, Any]:
    """Two-level tree with sizes, served from the ``files`` table (§10)."""
    prefix = path.strip("/")
    rows = await conn.fetch(
        """
        SELECT path, n_lines FROM files
         WHERE repo_id = $1 AND ($2 = '' OR path LIKE $2 || '/%' OR path = $2)
         ORDER BY path
        """,
        repo_id,
        prefix,
    )
    if not rows:
        return _err(f"no files under {path!r}")

    tree: dict[str, list[str]] = {}
    for r in rows:
        rel = str(r["path"])
        tail = rel[len(prefix) + 1 :] if prefix and rel.startswith(prefix) else rel
        parts = tail.split("/")
        if len(parts) == 1:
            tree.setdefault("", []).append(f"{parts[0]} ({r['n_lines']} lines)")
        else:
            tree.setdefault(parts[0], []).append(
                f"{'/'.join(parts[1:])} ({r['n_lines']} lines)"
            )

    lines: list[str] = []
    for d in sorted(tree):
        if d:
            lines.append(f"{d}/")
            lines.extend(f"  {e}" for e in sorted(tree[d]))
        else:
            lines.extend(sorted(tree[d]))
    return {"tree": "\n".join(lines)}
