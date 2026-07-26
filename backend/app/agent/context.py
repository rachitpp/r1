"""Context assembly for tool results (SPEC §7.4).

The "Called by" data deliberately kept out of embeddings in §2.4 is attached
here instead, at the moment a chunk or symbol body enters the model's context.
Keeping it out of the vector meant embeddings stay stable as the graph changes;
attaching it here means the agent still sees who calls what.
"""

from __future__ import annotations

from uuid import UUID

import asyncpg

from app.config import CALLED_BY_MAX
from app.db import queries


def format_called_by(
    callers: list[tuple[str, int, str]], total: int, *, limit: int = CALLED_BY_MAX
) -> str:
    """Render the trailing comment block, or ``""`` when there are no callers.

    ``# Called by: api/routes.py:34 (handle_login), api/routes.py:78 (refresh)``
    Truncates at ``limit`` with a ``+N more`` suffix — an unbounded list is a
    context-budget leak on hot symbols (SPEC §7.4).
    """
    if not callers:
        return ""
    shown = callers[:limit]
    parts = [f"{path}:{line} ({name})" for path, line, name in shown]
    block = "# Called by: " + ", ".join(parts)
    hidden = total - len(shown)
    if hidden > 0:
        block += f", +{hidden} more"
    return block


async def called_by_block(
    conn: asyncpg.Connection,
    repo_id: UUID,
    *,
    symbol_id: int | None,
    file_path: str,
    qualname: str | None,
    limit: int = CALLED_BY_MAX,
) -> str:
    """Called-by block for a chunk, resolving oversize parts via qualname.

    Goes through :func:`queries.resolve_symbol_id` rather than trusting
    ``symbol_id`` directly, so parts 2..n of a long function keep their caller
    annotations instead of silently rendering empty.
    """
    resolved = await queries.resolve_symbol_id(
        conn, repo_id, symbol_id=symbol_id, file_path=file_path, qualname=qualname
    )
    if resolved is None:
        return ""
    callers, total = await queries.implementation_callers(
        conn, repo_id, resolved, limit
    )
    return format_called_by(callers, total, limit=limit)
