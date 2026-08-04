"""What the repo stands on, and where it uses it (SPEC §26)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter

from app.api.deps import Conn, CurrentUser
from app.api.routes._common import require_owned_repo
from app.api.schemas import (
    DependenciesOut,
    DependencyOut,
    DependencyUse,
    DependencyUsesOut,
    UnusedDependency,
)
from app.config import (
    DEPENDENCY_MAX_PACKAGES,
    DEPENDENCY_MAX_USES,
)
from app.db import queries
from app.ingest.dependencies import distributions_for

router = APIRouter()


@router.get("/repos/{snapshot_id}/dependencies", response_model=DependenciesOut)
async def get_repo_dependencies(
    snapshot_id: UUID,
    conn: Conn,
    user: CurrentUser,
    include_tests: bool = False,
) -> DependenciesOut:
    """What this repo stands on, and what it declares (§26.2).

    Three lists rather than one, because the disagreements are the useful part:
    ``packages`` is what the code imports, ``undeclared`` is what it imports
    without asking for, and ``unused`` is what it asks for without importing.

    ``include_tests`` applies to the first two only. `unused` always counts a
    test-suite import as usage — reporting `pytest` as an unused dependency
    because tests were filtered out would be a false alarm of the worst kind:
    plausible, and wrong.
    """
    await require_owned_repo(conn, user["id"], snapshot_id)
    if not await queries.has_dependencies(conn, snapshot_id):
        # §26.3: ingested before the dependency pass existed. Say so rather
        # than let an empty list read as "this project has no dependencies".
        return DependenciesOut(
            indexed=False,
            include_tests=include_tests,
            packages=[],
            undeclared=[],
            unused=[],
            truncated=False,
        )

    rows = await queries.dependency_summary(
        conn, snapshot_id, include_tests=include_tests, limit=DEPENDENCY_MAX_PACKAGES
    )
    undeclared = await queries.undeclared_dependencies(
        conn, snapshot_id, include_tests=include_tests, limit=DEPENDENCY_MAX_PACKAGES
    )
    unused = await queries.unused_dependencies(
        conn, snapshot_id, limit=DEPENDENCY_MAX_PACKAGES
    )

    # Alias reconciliation (§26.2). SQL matched on the name as written, which
    # reports `python-dotenv` as unused *and* `dotenv` as undeclared — one
    # package, two contradictory findings. Resolving it here keeps the lookup
    # table in one language.
    declared = await queries.declared_by_name(conn, snapshot_id)
    used_distributions = {
        dist
        for r in [*rows, *undeclared]
        for dist in distributions_for(str(r["module"]))
    }

    packages = []
    for row in rows:
        package = DependencyOut.from_row(row)
        if not package.declared:
            match = next(
                (
                    declared[d]
                    for d in distributions_for(package.module)
                    if d in declared
                ),
                None,
            )
            if match is not None:
                # Carry the manifest across too: "declared" beside an empty
                # requirement reads as a bug in the panel.
                package.declared = True
                package.requirement = str(match["requirement"])
                package.sources = list(match["sources"] or [])
                package.extras = list(match["extras"] or [])
        packages.append(package)

    return DependenciesOut(
        indexed=True,
        include_tests=include_tests,
        packages=packages,
        undeclared=[
            str(r["module"])
            for r in undeclared
            if not any(
                d in declared for d in distributions_for(str(r["module"]))
            )
        ],
        unused=[
            UnusedDependency(
                name=str(r["name"]),
                requirement=str(r["requirement"]),
                sources=list(r["sources"] or []),
                extras=list(r["extras"] or []),
            )
            for r in unused
            if str(r["name"]) not in used_distributions
        ],
        truncated=len(rows) >= DEPENDENCY_MAX_PACKAGES,
    )


@router.get(
    "/repos/{snapshot_id}/dependencies/{module}", response_model=DependencyUsesOut
)
async def get_repo_dependency_uses(
    snapshot_id: UUID,
    module: str,
    conn: Conn,
    user: CurrentUser,
    include_tests: bool = False,
) -> DependencyUsesOut:
    """Every import site for one package (§26.2).

    An unknown module returns an empty list rather than a 404, for the §18.3
    reason one level down: "where is X used" and "X is not used" are the same
    answer, and a 404 would turn this into an existence oracle for package
    names in someone else's repo.
    """
    await require_owned_repo(conn, user["id"], snapshot_id)
    rows = await queries.dependency_uses(
        conn,
        snapshot_id,
        module,
        include_tests=include_tests,
        limit=DEPENDENCY_MAX_USES,
    )
    return DependencyUsesOut(
        module=module,
        include_tests=include_tests,
        uses=[
            DependencyUse(
                dotted=str(r["dotted"]),
                file_path=str(r["file_path"]),
                start_line=int(r["start_line"]),
                is_test=bool(r["is_test"]),
            )
            for r in rows
        ],
        truncated=len(rows) >= DEPENDENCY_MAX_USES,
    )
