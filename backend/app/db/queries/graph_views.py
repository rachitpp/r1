"""Module rollups and test linkage (SPEC §18), plus the two ranking queries
§19 and §22 read from the same graph."""

from __future__ import annotations

from uuid import UUID

import asyncpg

from app.config import ENTRY_POINT_FILENAMES

# --- §18 graph views: module rollup and test linkage -----------------------
#
# Read-only aggregations over the *existing* symbol graph. No new extraction,
# no ingest change, and — deliberately — no new agent tool: the answers here are
# deterministic SQL, so routing them through the model would spend from the
# eight-call budget (§7.2) to compute something a query already knows exactly.
#
# In Python a file *is* a module, so `symbols.file_path` is the module key
# directly rather than a derived package string. Nothing is parsed out of the
# path, which means the rollup cannot disagree with the graph it summarises.
#
# `include_tests` follows §6.3 flag-and-filter: extraction kept every symbol,
# the decision happens here, and the counterfactual stays one parameter away.


async def module_nodes(
    conn: asyncpg.Connection,
    snapshot_id: UUID,
    *,
    include_tests: bool,
    limit: int,
) -> list[tuple[str, int, int, int]]:
    """Modules ranked by fan-in: ``(path, n_symbols, fan_in, fan_out)``.

    Fan-in counts edges arriving from *other* files, which is the closest thing
    the graph has to "how much of this repo depends on this module". Same-file
    edges are excluded on both counts: a module calling itself says nothing
    about the architecture, and on a large file it would dominate the ranking.

    Ordered by fan-in with ``file_path`` as the tiebreaker — the 2026-07-29
    tie-ordering fix applies here too, or the truncation at ``limit`` would pick
    a different top-N per physical row order.
    """
    rows = await conn.fetch(
        """
        WITH scoped AS (
            SELECT id, file_path
              FROM symbols
             WHERE snapshot_id = $1
               AND (NOT is_test OR $2)
        ),
        cross_edges AS (
            SELECT f.file_path AS from_path, t.file_path AS to_path
              FROM edges e
              JOIN scoped f ON f.id = e.from_symbol
              JOIN scoped t ON t.id = e.to_symbol
             WHERE e.snapshot_id = $1
               AND f.file_path <> t.file_path
        )
        SELECT s.file_path AS path,
               count(*) AS n_symbols,
               (SELECT count(*) FROM cross_edges c WHERE c.to_path = s.file_path)
                 AS fan_in,
               (SELECT count(*) FROM cross_edges c WHERE c.from_path = s.file_path)
                 AS fan_out
          FROM scoped s
         GROUP BY s.file_path
         ORDER BY fan_in DESC, s.file_path
         LIMIT $3
        """,
        snapshot_id,
        include_tests,
        limit,
    )
    return [
        (str(r["path"]), int(r["n_symbols"]), int(r["fan_in"]), int(r["fan_out"]))
        for r in rows
    ]


async def module_edges(
    conn: asyncpg.Connection,
    snapshot_id: UUID,
    *,
    include_tests: bool,
    limit: int,
) -> list[tuple[str, str, str, int]]:
    """Module-to-module edges: ``(from_path, to_path, kind, weight)``.

    ``weight`` is how many symbol-level edges of that kind cross the pair, which
    is what lets a renderer draw a thick line for "these two modules are deeply
    coupled" and a thin one for a single import.
    """
    rows = await conn.fetch(
        """
        WITH scoped AS (
            SELECT id, file_path
              FROM symbols
             WHERE snapshot_id = $1
               AND (NOT is_test OR $2)
        )
        SELECT f.file_path AS from_path,
               t.file_path AS to_path,
               e.kind      AS kind,
               count(*)    AS weight
          FROM edges e
          JOIN scoped f ON f.id = e.from_symbol
          JOIN scoped t ON t.id = e.to_symbol
         WHERE e.snapshot_id = $1
           AND f.file_path <> t.file_path
         GROUP BY f.file_path, t.file_path, e.kind
         ORDER BY weight DESC, from_path, to_path, kind
         LIMIT $3
        """,
        snapshot_id,
        include_tests,
        limit,
    )
    return [
        (str(r["from_path"]), str(r["to_path"]), str(r["kind"]), int(r["weight"]))
        for r in rows
    ]


async def tests_covering_file(
    conn: asyncpg.Connection, snapshot_id: UUID, file_path: str, limit: int
) -> list[asyncpg.Record]:
    """Test symbols with an edge into each symbol defined in ``file_path``.

    The mirror of :func:`implementation_callers`, which excludes the test side
    precisely because it is noise when the question is "who uses this?". Here
    the test side *is* the question, so the filter is inverted rather than
    dropped — a caller from another implementation file is not coverage.

    Flat rows, one per (symbol, test) pair; the response model groups them. Both
    join sides are covered by ``edges_to`` and ``symbols_snapshot_name``.
    """
    return list(
        await conn.fetch(
            """
            SELECT impl.name       AS name,
                   impl.qualname   AS qualname,
                   impl.kind       AS kind,
                   impl.start_line AS start_line,
                   impl.end_line   AS end_line,
                   t.qualname      AS ref_qualname,
                   t.file_path     AS ref_file_path,
                   COALESCE(e.line, t.start_line) AS ref_line
              FROM symbols impl
              JOIN edges   e ON e.to_symbol = impl.id AND e.snapshot_id = $1
              JOIN symbols t ON t.id = e.from_symbol
             WHERE impl.snapshot_id = $1
               AND impl.file_path = $2
               AND t.is_test
             ORDER BY impl.start_line, t.file_path, ref_line, t.qualname
             LIMIT $3
            """,
            snapshot_id,
            file_path,
            limit,
        )
    )


async def entry_point_candidates(
    conn: asyncpg.Connection, snapshot_id: UUID, limit: int
) -> list[asyncpg.Record]:
    """Modules that look like where execution starts (SPEC §19.2).

    Two signals, unioned, because neither alone is reliable:

    * **Name.** ``__main__.py``, ``cli.py``, ``main.py``, ``app.py``,
      ``server.py`` — convention, and conventions are evidence.
    * **Shape.** Nothing inside the repo imports or calls it, yet it reaches
      out to plenty. That is the signature of a top of a call tree, and it is
      what catches an entry point named something this list has never heard of.

    Ranked with named files first: a file *called* ``cli.py`` that also has zero
    fan-in is the strongest possible candidate, and a name match with callers is
    still worth more than an unnamed leaf.
    """
    return list(
        await conn.fetch(
            """
            WITH scoped AS (
                SELECT id, file_path FROM symbols
                 WHERE snapshot_id = $1 AND NOT is_test
            ),
            fan AS (
                SELECT s.file_path AS path,
                       count(*) FILTER (
                           WHERE EXISTS (
                               SELECT 1 FROM edges e JOIN scoped f ON f.id = e.from_symbol
                                WHERE e.to_symbol = s.id AND f.file_path <> s.file_path
                           )
                       ) AS fan_in,
                       count(*) FILTER (
                           WHERE EXISTS (
                               SELECT 1 FROM edges e JOIN scoped t ON t.id = e.to_symbol
                                WHERE e.from_symbol = s.id AND t.file_path <> s.file_path
                           )
                       ) AS fan_out
                  FROM scoped s
                 GROUP BY s.file_path
            )
            SELECT fan.path,
                   fan.fan_in,
                   fan.fan_out,
                   (split_part(fan.path, '/', array_length(string_to_array(fan.path, '/'), 1))
                      = ANY($3::text[])) AS named,
                   -- The module symbol's real span, so the prompt can offer a
                   -- citable range. Without one the model invented `:1-1` for
                   -- every entry point and none of them validated.
                   COALESCE(m.start_line, 1) AS start_line,
                   COALESCE(m.end_line, 1) AS end_line
              FROM fan
              LEFT JOIN symbols m
                     ON m.snapshot_id = $1
                    AND m.file_path = fan.path
                    AND m.kind = 'module'
             WHERE split_part(fan.path, '/', array_length(string_to_array(fan.path, '/'), 1))
                     = ANY($3::text[])
                OR (fan.fan_in = 0 AND fan.fan_out > 0)
             ORDER BY named DESC, fan.fan_out DESC, fan.path
             LIMIT $2
            """,
            snapshot_id,
            limit,
            list(ENTRY_POINT_FILENAMES),
        )
    )


async def public_api_symbols(
    conn: asyncpg.Connection, snapshot_id: UUID, limit: int
) -> list[asyncpg.Record]:
    """The package's declared public surface, however it declares it.

    What a package puts at its top level is the closest thing a Python repo has
    to a public API, and a far better starting point for a newcomer than its
    largest file. But packages declare it two ways, and an early version of this
    query only saw one of them:

    * **Defined** in ``__init__.py`` — small packages do this.
    * **Re-exported** by it (``from ._api import get, post``) — which is what
      every non-trivial package does, and which lives in the graph as an
      ``imports`` edge *out of* ``__init__.py``.

    Measured on httpx, the definitions-only version returned **zero symbols**:
    its ``__init__.py`` is nothing but re-exports. A signal that goes silent on
    the package style it matters most for is not a signal. Both are unioned
    here, and the re-export side reports where the symbol actually lives, not
    the ``__init__`` line it was mentioned on — that is the file a reader needs
    to open.
    """
    return list(
        await conn.fetch(
            """
            SELECT DISTINCT ON (qualname)
                   name, qualname, kind, file_path, start_line, end_line
              FROM (
                    SELECT name, qualname, kind, file_path, start_line, end_line
                      FROM symbols
                     WHERE snapshot_id = $1
                       AND NOT is_test
                       AND right(file_path, 11) = '__init__.py'
                       AND kind <> 'module'
                    UNION
                    SELECT t.name, t.qualname, t.kind, t.file_path,
                           t.start_line, t.end_line
                      FROM edges e
                      JOIN symbols f ON f.id = e.from_symbol
                      JOIN symbols t ON t.id = e.to_symbol
                     WHERE e.snapshot_id = $1
                       AND e.kind = 'imports'
                       AND right(f.file_path, 11) = '__init__.py'
                       AND NOT t.is_test
                       AND t.kind <> 'module'
              ) surface
             ORDER BY qualname, file_path, start_line
             LIMIT $2
            """,
            snapshot_id,
            limit,
        )
    )


async def most_referenced_symbols(
    conn: asyncpg.Connection, snapshot_id: UUID, limit: int
) -> list[asyncpg.Record]:
    """The symbols the rest of the implementation leans on hardest (§19.2).

    Module fan-in says *which file* matters; this says which definition inside
    it does. Self-references are excluded on the same reasoning as the §18.2
    rollup — a class using its own methods says nothing about importance.
    """
    return list(
        await conn.fetch(
            """
            SELECT s.name, s.qualname, s.kind, s.file_path,
                   s.start_line, s.end_line, count(*) AS refs
              FROM edges e
              JOIN symbols s ON s.id = e.to_symbol
              JOIN symbols f ON f.id = e.from_symbol
             WHERE e.snapshot_id = $1
               AND NOT s.is_test AND NOT f.is_test
               AND f.file_path <> s.file_path
             GROUP BY s.id, s.name, s.qualname, s.kind, s.file_path,
                      s.start_line, s.end_line
             ORDER BY refs DESC, s.file_path, s.start_line
             LIMIT $2
            """,
            snapshot_id,
            limit,
        )
    )

async def implementation_covered_by_file(
    conn: asyncpg.Connection, snapshot_id: UUID, file_path: str, limit: int
) -> list[asyncpg.Record]:
    """What the *test* symbols in ``file_path`` reach in implementation code.

    The reverse direction of :func:`tests_covering_file`. Empty for a file that
    defines no test symbols, which is the correct answer rather than a special
    case: an implementation file does not "cover" anything.
    """
    return list(
        await conn.fetch(
            """
            SELECT DISTINCT
                   impl.qualname  AS ref_qualname,
                   impl.file_path AS ref_file_path,
                   COALESCE(e.line, impl.start_line) AS ref_line
              FROM symbols t
              JOIN edges   e    ON e.from_symbol = t.id AND e.snapshot_id = $1
              JOIN symbols impl ON impl.id = e.to_symbol
             WHERE t.snapshot_id = $1
               AND t.file_path = $2
               AND t.is_test
               AND NOT impl.is_test
             ORDER BY ref_file_path, ref_line, ref_qualname
             LIMIT $3
            """,
            snapshot_id,
            file_path,
            limit,
        )
    )

async def most_tested_files(
    conn: asyncpg.Connection, snapshot_id: UUID, limit: int
) -> list[asyncpg.Record]:
    """Implementation files the test suite exercises hardest (§22.2).

    The §18.3 linkage asked per file; this ranks across the repo. Tests are
    executable documentation, so "what is most tested" is a decent proxy for
    "what a newcomer is most expected to touch" — and unlike a heuristic about
    naming, it is a count of real resolved edges.

    Distinct *test symbols* rather than edges: one test calling a function four
    times is one test, and counting the calls would rank a loop above a suite.
    """
    return list(
        await conn.fetch(
            """
            SELECT impl.file_path,
                   count(DISTINCT t.id) AS n_tests,
                   min(impl.start_line) AS start_line
              FROM edges e
              JOIN symbols t    ON t.id = e.from_symbol
              JOIN symbols impl ON impl.id = e.to_symbol
             WHERE e.snapshot_id = $1
               AND t.is_test AND NOT impl.is_test
             GROUP BY impl.file_path
             ORDER BY n_tests DESC, impl.file_path
             LIMIT $2
            """,
            snapshot_id,
            limit,
        )
    )
