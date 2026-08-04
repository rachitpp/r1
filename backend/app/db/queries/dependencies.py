"""Third-party dependencies: what the repo imports and what it declares
(SPEC §26, FEATURE-IDEAS 2.5)."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

import asyncpg

# ---------------------------------------------------------------------------
# Dependencies (SPEC §26, FEATURE-IDEAS 2.5)
# ---------------------------------------------------------------------------

# name, requirement, source, extra
DependencyRowT = tuple[str, str, str, str | None]
# module, dotted, kind, file_path, start_line, is_test
DependencyUseRowT = tuple[str, str, str, str, int, bool]


async def clear_repo_dependencies(
    conn: asyncpg.Connection, snapshot_id: UUID
) -> None:
    """Delete declared dependencies and import sites for ``snapshot_id``."""
    await conn.execute("DELETE FROM dependencies WHERE snapshot_id = $1", snapshot_id)
    await conn.execute(
        "DELETE FROM dependency_uses WHERE snapshot_id = $1", snapshot_id
    )


async def insert_dependencies(
    conn: asyncpg.Connection, snapshot_id: UUID, rows: Sequence[DependencyRowT]
) -> int:
    """Batch-insert declared dependencies. Returns the number stored.

    ``ON CONFLICT DO NOTHING`` on the (name, source, extra) unique key: the
    same package pinned twice inside one manifest is one dependency, and a
    re-ingest of an unchanged snapshot is a no-op rather than an error.
    """
    if not rows:
        return 0
    result = await conn.fetch(
        """
        INSERT INTO dependencies (snapshot_id, name, requirement, source, extra)
        SELECT $1, r.name, r.requirement, r.source, r.extra
          FROM unnest($2::text[], $3::text[], $4::text[], $5::text[])
            AS r(name, requirement, source, extra)
        ON CONFLICT (snapshot_id, name, source, extra) DO NOTHING
        RETURNING id
        """,
        snapshot_id,
        [r[0] for r in rows],
        [r[1] for r in rows],
        [r[2] for r in rows],
        [r[3] for r in rows],
    )
    return len(result)


async def insert_dependency_uses(
    conn: asyncpg.Connection, snapshot_id: UUID, rows: Sequence[DependencyUseRowT]
) -> int:
    """Batch-insert import sites. Returns the number stored.

    No unique key and no conflict clause: two identical imports on different
    lines of one file are two real uses, and `clear_repo_dependencies` runs
    first on every ingest, so duplication across runs cannot accumulate.
    """
    if not rows:
        return 0
    await conn.execute(
        """
        INSERT INTO dependency_uses
          (snapshot_id, module, dotted, kind, file_path, start_line, is_test)
        SELECT $1, r.module, r.dotted, r.kind, r.file_path, r.start_line, r.is_test
          FROM unnest($2::text[], $3::text[], $4::text[], $5::text[],
                      $6::int[], $7::bool[])
            AS r(module, dotted, kind, file_path, start_line, is_test)
        """,
        snapshot_id,
        [r[0] for r in rows],
        [r[1] for r in rows],
        [r[2] for r in rows],
        [r[3] for r in rows],
        [r[4] for r in rows],
        [r[5] for r in rows],
    )
    return len(rows)


async def has_dependencies(conn: asyncpg.Connection, snapshot_id: UUID) -> bool:
    """Whether the dependency pass ran for this snapshot at all (§26.3).

    The distinction this preserves, exactly as `has_history` does for §20: a
    snapshot ingested before migration 015 has no rows, and reporting that as
    "this project has no dependencies" would be a confident lie. A repo that
    genuinely imports nothing third-party still has *stdlib* import rows, so
    the presence of any use row is the honest test for "the pass ran".
    """
    return bool(
        await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM dependency_uses WHERE snapshot_id = $1)",
            snapshot_id,
        )
    )


async def dependency_summary(
    conn: asyncpg.Connection,
    snapshot_id: UUID,
    *,
    include_tests: bool,
    limit: int,
) -> list[asyncpg.Record]:
    """Third-party packages used by this snapshot, most-imported first.

    Declared rows are joined on the normalised name, which is a *best effort*
    and stated as such: a distribution name and the module it ships need not
    match (`PyYAML` ships `yaml`), so `declared` false means "no manifest row
    under this name", not "undeclared".

    Only third-party rows: stdlib and first-party uses are stored (they are the
    same question in another bucket) but they are not what "what does this
    stand on" asks, and mixing them in would bury the answer under `os` and
    `typing`.
    """
    test_clause = "" if include_tests else "AND NOT u.is_test"
    return list(
        await conn.fetch(
            f"""
            SELECT u.module,
                   count(*)                        AS n_uses,
                   count(DISTINCT u.file_path)     AS n_files,
                   bool_or(d.name IS NOT NULL)     AS declared,
                   min(d.requirement)              AS requirement,
                   array_remove(array_agg(DISTINCT d.source), NULL) AS sources,
                   array_remove(array_agg(DISTINCT d.extra), NULL)  AS extras
              FROM dependency_uses u
              LEFT JOIN dependencies d
                     ON d.snapshot_id = u.snapshot_id
                    AND d.name = regexp_replace(lower(u.module), '[-_.]+', '-', 'g')
             WHERE u.snapshot_id = $1
               AND u.kind = 'third_party'
               {test_clause}
             GROUP BY u.module
             ORDER BY n_uses DESC, u.module
             LIMIT $2
            """,
            snapshot_id,
            limit,
        )
    )


async def undeclared_dependencies(
    conn: asyncpg.Connection, snapshot_id: UUID, *, include_tests: bool, limit: int
) -> list[asyncpg.Record]:
    """Packages imported by the code but named in no manifest (§26.2)."""
    test_clause = "" if include_tests else "AND NOT u.is_test"
    return list(
        await conn.fetch(
            f"""
            SELECT DISTINCT u.module
              FROM dependency_uses u
             WHERE u.snapshot_id = $1
               AND u.kind = 'third_party'
               {test_clause}
               AND NOT EXISTS (
                     SELECT 1 FROM dependencies d
                      WHERE d.snapshot_id = u.snapshot_id
                        AND d.name = regexp_replace(lower(u.module), '[-_.]+', '-', 'g')
                   )
             ORDER BY u.module
             LIMIT $2
            """,
            snapshot_id,
            limit,
        )
    )


async def unused_dependencies(
    conn: asyncpg.Connection, snapshot_id: UUID, *, limit: int
) -> list[asyncpg.Record]:
    """Packages a manifest declares that no import in the corpus reaches (§26.2).

    Tests are always counted as usage here regardless of `include_tests`: a
    package used only by the test suite is *used*, and reporting `pytest` as an
    unused dependency because tests were filtered out would be a false alarm of
    the worst kind — plausible, and wrong.
    """
    return list(
        await conn.fetch(
            """
            SELECT d.name, min(d.requirement) AS requirement,
                   array_agg(DISTINCT d.source) AS sources,
                   array_remove(array_agg(DISTINCT d.extra), NULL) AS extras
              FROM dependencies d
             WHERE d.snapshot_id = $1
               AND NOT EXISTS (
                     SELECT 1 FROM dependency_uses u
                      WHERE u.snapshot_id = d.snapshot_id
                        AND regexp_replace(lower(u.module), '[-_.]+', '-', 'g') = d.name
                   )
             GROUP BY d.name
             ORDER BY d.name
             LIMIT $2
            """,
            snapshot_id,
            limit,
        )
    )


async def dependency_uses(
    conn: asyncpg.Connection,
    snapshot_id: UUID,
    module: str,
    *,
    include_tests: bool,
    limit: int,
) -> list[asyncpg.Record]:
    """Every import site for one package — the "and where?" half of §26."""
    test_clause = "" if include_tests else "AND NOT is_test"
    return list(
        await conn.fetch(
            f"""
            SELECT dotted, file_path, start_line, is_test
              FROM dependency_uses
             WHERE snapshot_id = $1
               AND module = $2
               {test_clause}
             ORDER BY file_path, start_line
             LIMIT $3
            """,
            snapshot_id,
            module,
            limit,
        )
    )


async def declared_by_name(
    conn: asyncpg.Connection, snapshot_id: UUID
) -> dict[str, asyncpg.Record]:
    """Declared dependencies keyed by normalised name, for alias reconciliation.

    The `declared` flag on `dependency_summary` matches on the name as written,
    which misses `python-dotenv` vs `dotenv`. Reconciling that needs the alias
    table, the alias table lives in Python (`dependencies.MODULE_TO_DISTRIBUTION`),
    and pushing it into three SQL joins would put one lookup table in two
    languages. One extra cheap read instead.

    Returns the whole row, not just the name: a package matched through an alias
    must show its requirement and manifest like any other, or the panel says
    "declared" beside an empty spec.
    """
    rows = await conn.fetch(
        """
        SELECT name, min(requirement) AS requirement,
               array_agg(DISTINCT source) AS sources,
               array_remove(array_agg(DISTINCT extra), NULL) AS extras
          FROM dependencies
         WHERE snapshot_id = $1
         GROUP BY name
        """,
        snapshot_id,
    )
    return {str(r["name"]): r for r in rows}


async def file_texts(
    conn: asyncpg.Connection, snapshot_id: UUID, paths: Sequence[str]
) -> dict[str, str]:
    """Full contents for ``paths``, keyed by path (§27 grounding).

    One round trip for every cited file rather than one per citation: an answer
    commonly cites the same file three or four times, and the grounding check
    runs on the critical path of a response the user is already waiting on.
    """
    if not paths:
        return {}
    rows = await conn.fetch(
        "SELECT path, content FROM files WHERE snapshot_id = $1 AND path = ANY($2::text[])",
        snapshot_id,
        list(dict.fromkeys(paths)),
    )
    return {str(r["path"]): str(r["content"]) for r in rows}
