"""Call-hierarchy trace (SPEC §24)."""

from __future__ import annotations

from uuid import UUID

import asyncpg

# --- §24 call-hierarchy trace ----------------------------------------------


async def find_symbol(
    conn: asyncpg.Connection,
    snapshot_id: UUID,
    symbol: str,
    *,
    include_tests: bool = False,
) -> asyncpg.Record | None:
    """Resolve a user-supplied name to one symbol (§24.2).

    Shortest qualname wins, which is the same rule `expand_context` uses: a
    bare `Client` should find `pkg.Client` rather than
    `pkg.internal.compat.ClientShim`, and length is a decent proxy for "the one
    they meant".
    """
    return await conn.fetchrow(
        f"""
        SELECT id, name, qualname, kind, file_path, start_line, end_line
          FROM symbols
         WHERE snapshot_id = $1
           AND (name = $2 OR qualname = $2
                OR right(qualname, length($2) + 1) = '.' || $2)
           {"" if include_tests else "AND NOT is_test"}
         ORDER BY length(qualname), file_path
         LIMIT 1
        """,
        snapshot_id,
        symbol,
    )


async def symbol_seed_ids(
    conn: asyncpg.Connection, snapshot_id: UUID, root: asyncpg.Record
) -> list[int]:
    """The symbol itself, plus its members when it is a class (§24.2).

    Found by running the trace and disbelieving it: `httpx._client.Client`
    reached exactly **one** node, `BaseClient`, via `extends`. That is a true
    statement about the class *symbol* and a useless answer to "what does
    Client reach", because a class's calls live in its methods, and the chunker
    stores each method as its own symbol (§2.3).

    So a class is seeded with its members. Anything else seeds as itself —
    a function has no members and a prefix match on `f.` finds nothing
    anyway, but the branch keeps the intent legible.
    """
    root_id = int(root["id"])
    if str(root["kind"]) != "class":
        return [root_id]
    rows = await conn.fetch(
        """
        SELECT id FROM symbols
         WHERE snapshot_id = $1
           -- Prefix-compared, not LIKE: `_` is a wildcard and a qualname like
           -- `httpx._client.Client` is mostly underscores (2026-08-02).
           AND left(qualname, length($2) + 1) = $2 || '.'
        """,
        snapshot_id,
        str(root["qualname"]),
    )
    return [root_id, *(int(r["id"]) for r in rows)]


async def trace_graph(
    conn: asyncpg.Connection,
    snapshot_id: UUID,
    root_ids: list[int],
    *,
    direction: str,
    max_depth: int,
    limit: int,
    include_tests: bool = False,
) -> list[asyncpg.Record]:
    """Bounded transitive walk from ``root_ids`` (§24.2).

    Several roots rather than one because a class is seeded with its methods —
    see :func:`symbol_seed_ids`.

    ``direction`` is ``out`` (what this reaches) or ``in`` (what reaches this).

    **The cycle guard is not optional.** Call graphs have cycles — mutual
    recursion, a class whose method calls a helper that constructs the class —
    and a recursive CTE without one does not return a big result, it does not
    return. The visited path is carried per branch and re-entry is refused,
    which also gives every row a real chain back to the root rather than just a
    depth number.

    Ordered by depth then qualname so truncation keeps the *near* neighbours:
    on a hot symbol the first hop is the answer and hop four is noise.
    """
    # Direction only swaps which end of the edge is followed; everything else —
    # the guard, the caps, the ordering — is identical, so the two arms differ
    # by two column names rather than by being two queries.
    step_from, step_to = ("from_symbol", "to_symbol") if direction == "out" else (
        "to_symbol",
        "from_symbol",
    )
    test_clause = "" if include_tests else "AND NOT s.is_test"
    return list(
        await conn.fetch(
            f"""
            WITH RECURSIVE walk AS (
                -- Every seed starts with the WHOLE seed set marked as visited,
                -- not just itself: sibling methods of one class reach each
                -- other constantly, and without this the same node arrives by
                -- a dozen routes and the walk does far more work to produce
                -- rows the DISTINCT ON below throws away.
                SELECT r.id AS symbol_id,
                       0 AS depth,
                       $2::bigint[] AS seen,
                       NULL::text AS kind,
                       NULL::bigint AS parent
                  FROM unnest($2::bigint[]) AS r(id)
                UNION ALL
                SELECT e.{step_to},
                       w.depth + 1,
                       w.seen || e.{step_to},
                       e.kind,
                       w.symbol_id
                  FROM walk w
                  JOIN edges e
                    ON e.{step_from} = w.symbol_id
                   AND e.snapshot_id = $1
                 WHERE w.depth < $3
                   AND NOT (e.{step_to} = ANY(w.seen))
            )
            SELECT depth, kind, qualname, name, file_path,
                   start_line, end_line, via
              FROM (
                    -- Shallowest occurrence per symbol: a node reachable at
                    -- both depth 1 and depth 3 is a depth-1 node with a longer
                    -- alternative route, and reporting the long one would make
                    -- the trace look further from the root than it is.
                    SELECT DISTINCT ON (w.symbol_id)
                           w.symbol_id, w.depth, w.kind, s.qualname, s.name,
                           s.file_path, s.start_line, s.end_line,
                           p.qualname AS via
                      FROM walk w
                      JOIN symbols s ON s.id = w.symbol_id
                      LEFT JOIN symbols p ON p.id = w.parent
                     WHERE w.depth > 0
                       {test_clause}
                     ORDER BY w.symbol_id, w.depth
                   ) nearest
             -- Re-ordered before truncation: DISTINCT ON forces its own ORDER
             -- BY, so without this wrapper the LIMIT would keep an arbitrary
             -- set by symbol id rather than the near neighbours.
             ORDER BY depth, qualname
             LIMIT $4
            """,
            snapshot_id,
            root_ids,
            max_depth,
            limit,
        )
    )
