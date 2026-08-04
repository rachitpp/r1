"""Deterministic reads over the symbol graph: architecture (§18), coverage
(§18.3), trace (§24) and the onboarding checklist (§22). No model call —
if the graph can answer it exactly, it is a query, not a prompt."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query

from app.api.deps import Conn, CurrentUser
from app.api.routes._common import require_owned_repo
from app.api.schemas import (
    ArchitectureOut,
    ChecklistItemOut,
    ChecklistOut,
    CoverageOut,
    ModuleEdge,
    ModuleNode,
    SymbolRef,
    TraceNode,
    TraceOut,
)
from app.config import (
    ARCH_MAX_EDGES,
    ARCH_MAX_NODES,
    COVERAGE_MAX_LINKS,
    OVERVIEW_MAX_API_SYMBOLS,
    TRACE_MAX_DEPTH,
    TRACE_MAX_NODES,
)
from app.db import queries
from app.domain.checklist import build_checklist
from app.exceptions import (
    SymbolNotFoundError,
)

router = APIRouter()


@router.get("/repos/{snapshot_id}/architecture", response_model=ArchitectureOut)
async def get_repo_architecture(
    snapshot_id: UUID,
    conn: Conn,
    user: CurrentUser,
    include_tests: bool = Query(False),
) -> ArchitectureOut:
    """The module dependency rollup (§18.2).

    Two aggregations over the symbol graph that already exists — no model call,
    no tool budget, no ingest work. Deterministic, so the same snapshot always
    answers the same map; snapshots are immutable (§14.3), so a client may cache
    it for as long as it likes.

    Ranked by fan-in and truncated at the §12 caps rather than paginated: this
    is an overview, and page two of a module map is not an overview.
    """
    await require_owned_repo(conn, user["id"], snapshot_id)
    nodes = await queries.module_nodes(
        conn, snapshot_id, include_tests=include_tests, limit=ARCH_MAX_NODES
    )
    edges = await queries.module_edges(
        conn, snapshot_id, include_tests=include_tests, limit=ARCH_MAX_EDGES
    )
    return ArchitectureOut(
        nodes=[
            ModuleNode(path=p, n_symbols=n, fan_in=fi, fan_out=fo)
            for p, n, fi, fo in nodes
        ],
        edges=[
            ModuleEdge(from_path=f, to_path=t, kind=k, weight=w)
            for f, t, k, w in edges
        ],
        include_tests=include_tests,
        truncated=len(nodes) >= ARCH_MAX_NODES or len(edges) >= ARCH_MAX_EDGES,
    )

@router.get("/repos/{snapshot_id}/coverage", response_model=CoverageOut)
async def get_repo_coverage(
    snapshot_id: UUID,
    path: str,
    conn: Conn,
    user: CurrentUser,
) -> CoverageOut:
    """Test ↔ implementation links for one file (§18.3).

    An unknown ``path`` returns empty lists, not a 404: a file with no symbols
    and a file that is not in the index are the same answer to "what tests reach
    this?", and distinguishing them would make the endpoint an existence oracle
    for paths in someone else's repo — the §13.5 reasoning, one level down.
    """
    await require_owned_repo(conn, user["id"], snapshot_id)
    covered = await queries.tests_covering_file(
        conn, snapshot_id, path, COVERAGE_MAX_LINKS
    )
    covers = await queries.implementation_covered_by_file(
        conn, snapshot_id, path, COVERAGE_MAX_LINKS
    )
    return CoverageOut.from_rows(
        path,
        covered,
        covers,
        truncated=len(covered) >= COVERAGE_MAX_LINKS
        or len(covers) >= COVERAGE_MAX_LINKS,
    )

@router.get("/repos/{snapshot_id}/trace", response_model=TraceOut)
async def get_repo_trace(
    snapshot_id: UUID,
    symbol: str,
    conn: Conn,
    user: CurrentUser,
    direction: str = Query("out", pattern="^(in|out)$"),
    depth: int = Query(TRACE_MAX_DEPTH, ge=1, le=TRACE_MAX_DEPTH),
    include_tests: bool = Query(False),
) -> TraceOut:
    """Transitive call hierarchy from one symbol (§24.2).

    `expand_context` and `find_references` each do **one hop** and return code;
    this walks several and returns a **path**. The difference matters: "what
    does this reach, eventually" is a shape question, and answering it by
    pulling every reached body into a model context would spend the §12 token
    budget to say something a list of pointers says better.

    An endpoint rather than a seventh tool, per §18.1 — a bounded graph walk is
    exact SQL, so routing it through the model would spend from the budget of 8
    to compute what a recursive CTE already knows.

    `direction=out` is what this symbol reaches; `in` is what reaches it.
    """
    await require_owned_repo(conn, user["id"], snapshot_id)
    root = await queries.find_symbol(
        conn, snapshot_id, symbol, include_tests=include_tests
    )
    if root is None:
        raise SymbolNotFoundError(symbol)
    # A class is traced through its methods — see `symbol_seed_ids`.
    seeds = await queries.symbol_seed_ids(conn, snapshot_id, root)
    nodes = await queries.trace_graph(
        conn,
        snapshot_id,
        seeds,
        direction=direction,
        max_depth=depth,
        limit=TRACE_MAX_NODES,
        include_tests=include_tests,
    )
    return TraceOut(
        root=SymbolRef(
            qualname=root["qualname"],
            file_path=root["file_path"],
            line=root["start_line"],
        ),
        direction=direction,
        max_depth=depth,
        nodes=[TraceNode(**dict(n)) for n in nodes],
        truncated=len(nodes) >= TRACE_MAX_NODES,
    )


@router.get("/repos/{snapshot_id}/checklist", response_model=ChecklistOut)
async def get_repo_checklist(
    snapshot_id: UUID,
    conn: Conn,
    user: CurrentUser,
) -> ChecklistOut:
    """"The first five things to understand about this repo" (§22.2).

    **No model call**, which is the whole design. FEATURE-IDEAS pairs 6.5 with
    3.1 and the obvious reading is "a second generated document" — a second
    request per snapshot against a tier that allows twenty a day. §18.1 already
    settled it: if the symbol graph can answer it exactly, it is a query. Which
    module everything imports, where execution starts, what the package
    exports, what the tests hit hardest — all `GROUP BY`s §19 was already
    running. A model would contribute phrasing, and phrasing is a template.

    Deterministic over an immutable snapshot (§14.3), so a client may cache it
    for as long as it holds the id.
    """
    await require_owned_repo(conn, user["id"], snapshot_id)
    file_rows = await conn.fetch(
        "SELECT path, n_lines FROM files WHERE snapshot_id = $1", snapshot_id
    )
    items = build_checklist(
        entry_points=await queries.entry_point_candidates(conn, snapshot_id, 1),
        # `module_nodes` hands back positional tuples, not records — adapt
        # here rather than teaching the builder two row shapes.
        modules=[
            {"path": p, "n_symbols": n, "fan_in": fi, "fan_out": fo}
            for p, n, fi, fo in await queries.module_nodes(
                conn, snapshot_id, include_tests=False, limit=1
            )
        ],
        key_symbols=await queries.most_referenced_symbols(conn, snapshot_id, 1),
        # More candidates than the step shows: `_package_surface` filters to
        # this package and dedupes, and needs something to filter.
        api_symbols=await queries.public_api_symbols(
            conn, snapshot_id, OVERVIEW_MAX_API_SYMBOLS
        ),
        tested_files=await queries.most_tested_files(conn, snapshot_id, 1),
        n_lines_of={str(r["path"]): int(r["n_lines"]) for r in file_rows},
    )
    return ChecklistOut(
        items=[ChecklistItemOut(**vars(i)) for i in items],
    )
