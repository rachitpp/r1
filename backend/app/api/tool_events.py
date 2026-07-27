"""Tool results -> §9 ``tool_result`` payloads: summaries and locations only.

SPEC §9 is explicit that **no code bodies go over the wire**. The frontend
renders the agent's steps from these summaries and fetches code on demand via
``GET /repos/{id}/files``, so a chunk of source in a ``tool_result`` is not a
convenience — it is duplicated transport for something the viewer already has,
on every tool call, for every question.

This module is therefore an allowlist, not a filter: it names the fields it
copies out of each tool's JSON, and everything else — ``code``, ``content``,
``preview``, ``tree`` — never leaves. A new tool with a new payload shape gets a
generic count summary and no locations until someone teaches it here.
"""

from __future__ import annotations

import json
from typing import Any, TypedDict


class Location(TypedDict):
    file_path: str
    start_line: int
    end_line: int


def _loc(file_path: Any, start: Any, end: Any) -> Location:
    return {
        "file_path": str(file_path),
        "start_line": int(start),
        "end_line": int(end),
    }


def _rows(data: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = data.get(key)
    return [r for r in value if isinstance(r, dict)] if isinstance(value, list) else []


def summarize_tool_result(payload: str) -> tuple[str, list[Location]]:
    """Return ``(summary, locations)`` for one tool's JSON result string.

    Shapes come from SPEC §7.1: ``hits`` (search_code), ``matches``
    (get_definition), ``references`` (find_references), ``edges``/``symbols``
    (expand_context), ``path``/``start_line`` (read_file), ``tree``
    (list_directory), or ``error`` from any of them.
    """
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return "result unavailable", []
    if not isinstance(data, dict):
        return "result unavailable", []

    if "error" in data:
        return f"error: {data['error']}", []

    if "hits" in data:
        hits = _rows(data, "hits")
        locations = [
            _loc(h["file_path"], h["start_line"], h["end_line"])
            for h in hits
            if {"file_path", "start_line", "end_line"} <= h.keys()
        ]
        return f"{len(hits)} hit{'s' if len(hits) != 1 else ''}", locations

    if "matches" in data:
        matches = _rows(data, "matches")
        locations = [
            _loc(m["file_path"], m["start_line"], m["end_line"])
            for m in matches
            if {"file_path", "start_line", "end_line"} <= m.keys()
        ]
        names = ", ".join(str(m.get("qualname", "?")) for m in matches[:3])
        suffix = f": {names}" if names else ""
        return f"{len(matches)} definition(s){suffix}", locations

    if "references" in data:
        refs = _rows(data, "references")
        locations = [
            _loc(r["file_path"], r["line"], r["line"])
            for r in refs
            if {"file_path", "line"} <= r.keys()
        ]
        return f"{len(refs)} reference(s)", locations

    if "symbols" in data and "edges" in data:
        symbols = _rows(data, "symbols")
        locations = [
            _loc(s["file_path"], s["start_line"], s["end_line"])
            for s in symbols
            if {"file_path", "start_line", "end_line"} <= s.keys()
        ]
        note = " (truncated)" if data.get("truncated") else ""
        n_edges = len(_rows(data, "edges"))
        return (
            f"{len(symbols)} symbol(s) over {n_edges} edge(s){note}",
            locations,
        )

    if "path" in data and "start_line" in data:
        loc = _loc(data["path"], data["start_line"], data["end_line"])
        return (
            f"{loc['file_path']}:{loc['start_line']}-{loc['end_line']}",
            [loc],
        )

    if "tree" in data:
        n = len(str(data["tree"]).splitlines())
        return f"{n} entr{'y' if n == 1 else 'ies'}", []

    return "ok", []
