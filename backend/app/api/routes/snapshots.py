"""Sibling snapshots and the structural diff between two of them (SPEC §28)."""

from __future__ import annotations

from uuid import UUID

import asyncpg
from fastapi import APIRouter

from app.api.deps import Conn, CurrentUser
from app.api.routes._common import require_owned_repo
from app.api.schemas import (
    ChangedSymbol,
    CompareCommit,
    CompareOut,
    SiblingSnapshot,
    SiblingsOut,
    SnapshotRef,
)
from app.config import (
    COMPARE_MAX_COMMITS,
    COMPARE_MAX_ITEMS,
)
from app.db import queries
from app.exceptions import (
    SnapshotsNotComparable,
)

router = APIRouter()


@router.get("/repos/{snapshot_id}/snapshots", response_model=SiblingsOut)
async def list_sibling_snapshots(
    snapshot_id: UUID, conn: Conn, user: CurrentUser
) -> SiblingsOut:
    """Other snapshots of this repo the caller could compare against (§28.3).

    Exists so the UI can offer a comparison without the reader having to know
    snapshot ids. Same-strategy only: an `ast` corpus and a `naive` one are not
    comparable, and putting the pairing in a picker would only produce a 400 on
    click.
    """
    await require_owned_repo(conn, user["id"], snapshot_id)
    rows = await queries.sibling_snapshots(conn, user["id"], snapshot_id)
    return SiblingsOut(
        siblings=[
            SiblingSnapshot(
                id=r["id"],
                commit_sha=r["commit_sha"],
                status=str(r["status"]),
                created_at=r["created_at"],
            )
            for r in rows
        ]
    )


@router.get("/repos/{snapshot_id}/compare", response_model=CompareOut)
async def compare_snapshots(
    snapshot_id: UUID,
    base: UUID,
    conn: Conn,
    user: CurrentUser,
) -> CompareOut:
    """What changed between two snapshots of the same repo (§28.2).

    Reads as "this snapshot, compared against `base`" — the path names what you
    are looking at and the query names what you are looking back to, which is
    the direction a reader arrives from a repo page with.

    A *structural* diff. `git diff` answers "which lines changed" and answers it
    better; what only this can answer is what the **index** now holds — which
    files, symbols and third-party packages exist that did not before. Costs no
    model call: four SQL statements over two immutable corpora.
    """
    await require_owned_repo(conn, user["id"], snapshot_id)
    await require_owned_repo(conn, user["id"], base)
    meta = await queries.snapshot_meta(conn, [snapshot_id, base])
    head_row, base_row = meta[snapshot_id], meta[base]

    # Two guards, both because a confident wrong answer is worse than a refusal.
    if head_row["source_id"] != base_row["source_id"]:
        raise SnapshotsNotComparable(
            "those snapshots are of different repositories"
        )
    if head_row["strategy"] != base_row["strategy"]:
        # `naive` stores no symbols at all (§2.7), so this pairing would report
        # every symbol in the repo as deleted — a number that looks like a
        # finding and is an artefact of the question.
        raise SnapshotsNotComparable(
            f"those snapshots were chunked differently "
            f"({base_row['strategy']} vs {head_row['strategy']}); "
            "a comparison across strategies measures the chunker, not the code"
        )

    files_added, files_removed = await queries.compare_files(
        conn, base, snapshot_id, COMPARE_MAX_ITEMS
    )
    sym_added, sym_removed = await queries.compare_symbols(
        conn, base, snapshot_id, COMPARE_MAX_ITEMS
    )
    deps_added, deps_removed = await queries.compare_dependencies(
        conn, base, snapshot_id, COMPARE_MAX_ITEMS
    )
    commits_indexed = await queries.has_history(
        conn, snapshot_id
    ) and await queries.has_history(conn, base)
    commits = (
        await queries.commits_between(conn, base, snapshot_id, COMPARE_MAX_COMMITS)
        if commits_indexed
        else []
    )

    def _ref(row: asyncpg.Record) -> SnapshotRef:
        return SnapshotRef(
            id=row["id"],
            commit_sha=row["commit_sha"],
            strategy=str(row["strategy"]),
            created_at=row["created_at"],
        )

    return CompareOut(
        base=_ref(base_row),
        head=_ref(head_row),
        files_added=files_added,
        files_removed=files_removed,
        symbols_added=[
            ChangedSymbol(
                qualname=str(r["qualname"]),
                kind=str(r["kind"]),
                file_path=str(r["file_path"]),
            )
            for r in sym_added
        ],
        symbols_removed=[
            ChangedSymbol(
                qualname=str(r["qualname"]),
                kind=str(r["kind"]),
                file_path=str(r["file_path"]),
            )
            for r in sym_removed
        ],
        dependencies_added=deps_added,
        dependencies_removed=deps_removed,
        commits_indexed=commits_indexed,
        commits=[
            CompareCommit(
                sha=str(c["sha"]),
                author_name=str(c["author_name"]),
                authored_at=c["authored_at"],
                subject=str(c["subject"]),
            )
            for c in commits
        ],
        truncated=(
            len(files_added) + len(files_removed) >= COMPARE_MAX_ITEMS
            or len(sym_added) + len(sym_removed) >= COMPARE_MAX_ITEMS
        ),
    )
