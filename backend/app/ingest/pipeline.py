"""The one ingest pipeline, shared by the CLI and the ARQ task (SPEC §10).

    clone -> filter -> parse -> symbols -> embed -> backfill

Phase 2/3 ran this inline in ``app/ingest/cli.py``; Phase 4 needs the identical
sequence inside a background job, so it moved here and the CLI became a thin
wrapper (Phase 4 Reconciliation 2). One copy means the queue cannot drift from
what the CLI proved out.

**State machine** (§10, widened in Phase 4 with ``linking``)::

    queued -> cloning -> parsing -> linking -> embedding -> ready | failed

The symbol pass gets its own state because it is 30-40 % of wall time on a
mid-size repo and is otherwise invisible: a user watching "parsing" not move
for 34 seconds assumes the job is wedged.

Two ordering constraints are not negotiable:

1. **Everything up to and including the symbol pass runs inside the clone
   context.** Jedi resolves against real files on disk (§6.1), and the workdir
   is deleted when the context exits.
2. **``symbol_id`` backfill runs after the chunk insert**, because it joins
   chunks to symbols; the symbols themselves are extracted before embedding so
   the graph is complete even if embedding later fails.

Failure handling belongs to the caller: this function raises, and the ARQ task
(or the CLI) decides what to write to ``repos.error``.
"""

from __future__ import annotations

import dataclasses
import logging
import time
from collections.abc import Callable
from pathlib import Path
from uuid import UUID

import asyncpg

from app.config import PROGRESS_EVERY_N, get_settings
from app.db import queries
from app.db.pool import close_pool, create_pool
from app.exceptions import IngestError, SnapshotSuperseded
from app.ingest.chunker import Chunk, chunk_file
from app.ingest.clone import cloned_repo
from app.ingest.dependencies import (
    KIND_THIRD_PARTY,
    ImportSite,
    extract_imports,
    first_party_names,
    parse_manifests,
)
from app.ingest.embedder import get_embedder
from app.ingest.filters import (
    SelectionResult,
    SourceFile,
    is_test_path,
    select_files,
)
from app.ingest.history import walk_history
from app.ingest.naive import naive_chunk_file
from app.ingest.parser import ParsedFile, parse_file, parse_tree
from app.ingest.symbols import (
    EdgeStats,
    extract_edges,
    extract_symbols,
    import_roots,
)
from app.ingest.tokens import HeuristicTokenCounter

logger = logging.getLogger(__name__)

# How many chunks to embed per model call before inserting a batch. Independent
# of PROGRESS_EVERY_N (the progress-write cadence); purely an encode/insert
# granularity.
EMBED_BATCH = 64

# Called with one human-readable line per pipeline milestone. The CLI prints
# these; the worker logs them.
ProgressLog = Callable[[str], None]

# Chunking strategies. "ast" is the product; "naive" is the Phase 6 measurement
# baseline and is reachable only from the CLI (SPEC §2.7).
STRATEGIES = ("ast", "naive")

# The `#naive` URL fragment is gone (SPEC §14.6). It existed only because
# `repos.url` was UNIQUE and the baseline corpus needed a distinct key to
# coexist with the AST corpus; a snapshot carries `strategy` instead, so the
# two live side by side at the same commit with no string mangling. A refactor
# that deletes a workaround rather than relocating it is evidence the model
# fits — the opposite outcome would have been evidence against.


@dataclasses.dataclass
class IngestStats:
    """What one completed ingest produced.

    ``chunks`` is carried so the CLI can dump or sample the exact chunk set that
    was stored; the worker ignores it.
    """

    name: str
    snapshot_id: str
    head_sha: str
    default_branch: str
    selection: SelectionResult
    chunks: list[Chunk]
    n_syntax_errors: int
    heuristic_chunk_count: int
    parse_elapsed_s: float
    embed_elapsed_s: float
    db_elapsed_s: float
    n_symbols: int = 0
    n_symbols_test: int = 0
    n_edges: int = 0
    edge_stats: EdgeStats | None = None
    n_chunks_linked: int = 0
    graph_elapsed_s: float = 0.0


def _chunk_to_row(chunk: Chunk, embedding: list[float]) -> queries.ChunkRow:
    return (
        chunk.file_path,
        is_test_path(chunk.file_path),
        chunk.symbol,
        chunk.kind,
        chunk.part,
        chunk.n_parts,
        chunk.start_line,
        chunk.end_line,
        chunk.header,
        chunk.code,
        embedding,
    )


async def _store_graph(
    conn: asyncpg.Connection,
    snapshot_id: UUID,
    repo_dir: Path,
    selection: SelectionResult,
    parsed: list[ParsedFile],
) -> tuple[int, int, int, EdgeStats]:
    """Extract and store symbols + edges (SPEC §6.1). Backfill happens later.

    Must run **inside** the clone context: Jedi resolves against real files on
    disk, and the workdir is deleted when the context exits.

    Edges whose endpoints didn't survive symbol insertion are dropped rather
    than errored — a resolution can land on a line no chunk covers (a module
    docstring, a bare assignment), and that is a miss, not a failure.
    """
    symbols = extract_symbols(parsed)
    edges, stats = extract_edges(repo_dir, selection.files, symbols)

    await queries.insert_symbols(
        conn,
        snapshot_id,
        [
            (s.name, s.qualname, s.kind, s.file_path, s.start_line, s.end_line, s.is_test)
            for s in symbols
        ],
    )
    id_of = await queries.symbol_id_map(conn, snapshot_id)
    edge_rows: list[queries.EdgeRowT] = [
        (id_of[e.from_key], id_of[e.to_key], e.kind, e.line)
        for e in edges
        if e.from_key in id_of and e.to_key in id_of
    ]
    await queries.insert_edges(conn, snapshot_id, edge_rows)

    n_test = sum(1 for s in symbols if s.is_test)
    return len(symbols), n_test, len(edge_rows), stats


async def store_dependencies(
    conn: asyncpg.Connection,
    snapshot_id: UUID,
    repo_dir: Path,
    sources: list[SourceFile],
) -> tuple[int, int, int]:
    """Store declared dependencies and import sites (SPEC §26).

    Returns ``(declared, uses, third_party_packages)``.

    Must run **inside** the clone context: `pyproject.toml` and
    `requirements*.txt` are not `*.py`, so `filters.py` never selected them and
    they exist nowhere but the workdir the cleanup is about to delete.

    Re-parses rather than reusing `ParsedFile`, which carries only the
    *top-level* import statements as text, for the module chunk's header — no
    line numbers, and nothing from inside a function, where optional
    dependencies usually live. `extract_edges` re-parses for the same reason;
    a tree-sitter parse is cheap next to the embedding pass.
    """
    roots = import_roots(repo_dir)
    first_party = first_party_names(repo_dir, roots)

    sites: list[ImportSite] = []
    for source in sources:
        tree = parse_tree(source.text)
        if tree is None or tree.root_node.has_error:
            continue
        sites.extend(extract_imports(tree.root_node, source.path, first_party))

    declared = parse_manifests(repo_dir)
    n_declared = await queries.insert_dependencies(
        conn,
        snapshot_id,
        [(d.name, d.raw, d.source, d.extra) for d in declared],
    )
    n_uses = await queries.insert_dependency_uses(
        conn,
        snapshot_id,
        [
            (s.module, s.dotted, s.kind, s.file_path, s.line, s.is_test)
            for s in sites
        ],
    )
    third_party = {s.module for s in sites if s.kind == KIND_THIRD_PARTY}
    return n_declared, n_uses, len(third_party)


async def store_history(
    conn: asyncpg.Connection,
    snapshot_id: UUID,
    repo_dir: Path,
    rev: str | None = None,
) -> tuple[int, int]:
    """Read and store the commit log (SPEC §20.1). Returns ``(commits, touches)``.

    Must run **inside** the clone context — it reads `.git`, which the workdir
    cleanup removes.

    ``rev`` walks from a named commit rather than HEAD. Ingest leaves it unset
    (it walks the clone it just made, whose HEAD *is* the snapshot's commit);
    `scripts/backfill_history.py` sets it, because an existing snapshot is
    pinned to a commit the repo has since moved past.

    Touch rows are keyed to commits by sha, and a sha absent from the insert
    map means ``ON CONFLICT DO NOTHING`` skipped it because history for this
    snapshot was already stored. Dropping those touches is correct: the rows
    they would duplicate are already there.
    """
    commits, touches = walk_history(repo_dir, rev=rev)
    if not commits:
        return 0, 0

    id_of = await queries.insert_commits(
        conn,
        snapshot_id,
        [
            (c.sha, c.author_name, c.author_email, c.authored_at,
             c.subject, c.body, c.is_merge)
            for c in commits
        ],
    )
    file_rows: list[queries.CommitFileRowT] = [
        (id_of[t.sha], t.file_path, t.insertions, t.deletions)
        for t in touches
        if t.sha in id_of
    ]
    await queries.insert_commit_files(conn, snapshot_id, file_rows)
    return len(id_of), len(file_rows)


async def run_ingest(
    snapshot_id: UUID,
    *,
    pool: asyncpg.Pool | None = None,
    build_graph: bool = True,
    strategy: str = "ast",
    rev: str | None = None,
    log: ProgressLog | None = None,
) -> IngestStats:
    """Run the full pipeline for an existing ``repo_snapshots`` row and store it.

    The row must already exist (``POST /repos`` or the CLI creates it); this
    function owns everything after that, including the §10 status transitions and
    progress counters. Raises on failure with the row left mid-flight — the
    caller records ``failed`` (the worker) so the error text has one owner.

    ``pool`` lets a long-lived caller (the worker) share its pool; when omitted
    one is created and closed here.

    ``strategy`` selects the chunker. ``"naive"`` is the Phase 6 measurement
    baseline (SPEC §2.7) and is never passed by the API or the worker.
    """
    if strategy not in STRATEGIES:
        raise IngestError(f"unknown chunk strategy {strategy!r}")
    say: ProgressLog = log if log is not None else (lambda _msg: None)
    own_pool = pool is None
    if pool is None:
        pool = await create_pool(get_settings().DATABASE_URL)
    try:
        async with pool.acquire() as conn:
            return await _run(
                conn,
                snapshot_id,
                build_graph=build_graph,
                strategy=strategy,
                rev=rev,
                say=say,
            )
    finally:
        if own_pool:
            await close_pool(pool)


async def _run(
    conn: asyncpg.Connection,
    snapshot_id: UUID,
    *,
    build_graph: bool,
    strategy: str,
    rev: str | None,
    say: ProgressLog,
) -> IngestStats:
    row = await queries.source_of(conn, snapshot_id)
    if row is None:
        raise IngestError(f"no snapshot {snapshot_id}")
    # The URL is already canonical — no fragment to strip, because there is no
    # longer a fragment to add (§14.6).
    clone_url = str(row["url"])

    embedder = get_embedder()  # also the real TokenCounter for oversize splits
    await queries.start_ingest(conn, snapshot_id, status="cloning")
    say(f"cloning {clone_url}")

    parse_start = time.perf_counter()
    with cloned_repo(clone_url, rev) as info:
        # §14.4, the half of dedup that needs the clone. The commit SHA is not
        # knowable before this point — checking earlier would dedup on URL,
        # which is wrong (two users, two commits), and checking after ingesting
        # would dedup nothing. This is where a thousand submissions of one
        # popular repo collapse into a single stored corpus.
        kept = await queries.find_ready_snapshot(
            conn, row["source_id"], info.head_sha, strategy
        )
        if kept is not None and kept != snapshot_id:
            await queries.supersede_snapshot(conn, snapshot_id, kept)
            say(f"already ingested at {info.head_sha[:12]}; reusing snapshot {kept}")
            raise SnapshotSuperseded(kept)

        await queries.set_repo_clone_info(
            conn,
            snapshot_id,
            # The plain upstream name: the AST and naive corpora of one repo
            # now share a source and are told apart by `strategy` (§14.6),
            # so the name no longer has to carry the distinction.
            name=info.name,
            head_sha=info.head_sha,
            default_branch=info.default_branch,
        )

        # --- parsing -------------------------------------------------------
        await queries.set_repo_status(conn, snapshot_id, "parsing")
        selection = select_files(info.path)
        say(f"selected {selection.n_kept} of {selection.n_candidates} candidate files")

        # A snapshot is written once and frozen (§14.3), so there is normally
        # nothing here to clear — this is a fresh row. The clear survives for
        # one case only: an ARQ retry re-entering a snapshot whose first
        # attempt died partway. Those rows are unreachable (a non-`ready`
        # snapshot is not servable), so clearing them races with no reader,
        # which is exactly what was untrue before the split.
        await queries.clear_repo_graph(conn, snapshot_id)
        await queries.clear_repo_history(conn, snapshot_id)
        await queries.clear_repo_dependencies(conn, snapshot_id)
        await queries.clear_repo_content(conn, snapshot_id)
        file_rows = [(f.path, f.text, f.n_lines) for f in selection.files]
        await queries.insert_files(conn, snapshot_id, file_rows)
        await queries.set_repo_progress(
            conn, snapshot_id, files_total=len(file_rows), files_parsed=0
        )

        parsed: list[ParsedFile] = []
        n_syntax_errors = 0
        last_written = 0
        for i, source in enumerate(selection.files, start=1):
            pf = parse_file(source)
            if pf is None:
                n_syntax_errors += 1
            else:
                parsed.append(pf)
            if i - last_written >= PROGRESS_EVERY_N or i == len(selection.files):
                await queries.set_repo_progress(conn, snapshot_id, files_parsed=i)
                last_written = i
        say(f"parsed {len(parsed)} files ({n_syntax_errors} syntax errors)")

        chunks: list[Chunk] = []
        if strategy == "naive":
            # SPEC §2.7 baseline. The parse above still runs so the §10 progress
            # contract and the syntax-error count are identical across the two
            # corpora, but no chunk here touches the AST.
            for source in selection.files:
                chunks.extend(naive_chunk_file(source))
            # No tokenizer is involved in fixed-window splitting, so there is no
            # heuristic-vs-real delta to report.
            heuristic_chunk_count = len(chunks)
        else:
            for pf in parsed:
                chunks.extend(chunk_file(pf, embedder))
            # Same chunker, heuristic counter — count only, to report the delta
            # between the heuristic and the real tokenizer (Phase 2
            # Reconciliation 2).
            heuristic = HeuristicTokenCounter()
            heuristic_chunk_count = sum(len(chunk_file(pf, heuristic)) for pf in parsed)
        parse_elapsed = time.perf_counter() - parse_start
        await queries.set_repo_progress(
            conn, snapshot_id, chunks_total=len(chunks), chunks_embedded=0
        )

        # --- linking -------------------------------------------------------
        n_symbols = n_symbols_test = n_edges = 0
        graph_stats: EdgeStats | None = None
        graph_elapsed = 0.0
        if build_graph:
            await queries.set_repo_status(conn, snapshot_id, "linking")
            say("building symbol graph (tree-sitter sites, Jedi resolve)…")
            graph_start = time.perf_counter()
            n_symbols, n_symbols_test, n_edges, graph_stats = await _store_graph(
                conn, snapshot_id, info.path, selection, parsed
            )
            graph_elapsed = time.perf_counter() - graph_start
            say(
                f"symbols {n_symbols} ({n_symbols - n_symbols_test} impl / "
                f"{n_symbols_test} test), edges {n_edges} ({graph_elapsed:.0f}s)"
            )

        # --- history -------------------------------------------------------
        # Inside the clone context, and after the graph so a history failure
        # cannot cost a corpus that is otherwise complete. `walk_history`
        # already swallows its own errors (§20.1); this is belt and braces
        # about the storage half.
        #
        # Deliberately outside `if build_graph`: history describes the *repo*,
        # not the chunking strategy, and §2.7's naive baseline sits at the same
        # commit. Storing it twice is the duplication `012` accepts on purpose.
        try:
            n_commits, n_touches = await store_history(conn, snapshot_id, info.path)
            if n_commits:
                say(f"history {n_commits} commits, {n_touches} file touches")
        except Exception as exc:  # noqa: BLE001 - enrichment must not fail ingest
            logger.warning("history pass failed for %s: %s", snapshot_id, exc)

        # --- dependencies --------------------------------------------------
        # Inside the clone context, for a harder reason than history: the
        # manifests are not `*.py`, so `filters.py` never selected them and
        # they exist nowhere but this workdir. After the graph and guarded the
        # same way — a repo whose pyproject.toml does not parse still has a
        # perfectly good corpus, and must not lose it over that.
        #
        # Outside `if build_graph`, like history: what a repo imports is a
        # property of the repo, not of the chunking strategy.
        try:
            n_declared, n_uses, n_third = await store_dependencies(
                conn, snapshot_id, info.path, selection.files
            )
            if n_uses:
                say(
                    f"dependencies {n_third} third-party package(s), "
                    f"{n_declared} declared, {n_uses} import sites"
                )
        except Exception as exc:  # noqa: BLE001 - enrichment must not fail ingest
            logger.warning("dependency pass failed for %s: %s", snapshot_id, exc)

        # --- embedding -----------------------------------------------------
        # The clone is no longer needed from here on, but staying inside the
        # context costs only disk and keeps the exit path single.
        await queries.set_repo_status(conn, snapshot_id, "embedding")
        db_start = time.perf_counter()
        total = len(chunks)
        done = 0
        last_written = 0
        embed_start = time.perf_counter()
        for i in range(0, total, EMBED_BATCH):
            batch = chunks[i : i + EMBED_BATCH]
            vectors = embedder.encode([c.text for c in batch])
            rows = [
                _chunk_to_row(c, v) for c, v in zip(batch, vectors, strict=True)
            ]
            await queries.insert_chunks(conn, snapshot_id, rows)
            done += len(batch)
            if done - last_written >= PROGRESS_EVERY_N or done == total:
                await queries.set_repo_progress(conn, snapshot_id, chunks_embedded=done)
                rate = done / max(time.perf_counter() - embed_start, 1e-9)
                say(f"embedded {done}/{total} chunks ({rate:.0f}/s)")
                last_written = done
        embed_elapsed = time.perf_counter() - embed_start

        n_linked = 0
        if build_graph:
            n_linked = await queries.backfill_chunk_symbol_ids(conn, snapshot_id)
            say(f"linked {n_linked} chunks to a symbol")

        await queries.finalize_repo(
            conn,
            snapshot_id,
            files_total=len(file_rows),
            files_parsed=len(parsed),
            chunks_total=total,
            status="ready",
        )
        db_elapsed = time.perf_counter() - db_start - embed_elapsed

        return IngestStats(
            name=info.name,
            snapshot_id=str(snapshot_id),
            head_sha=info.head_sha,
            default_branch=info.default_branch,
            selection=selection,
            chunks=chunks,
            n_syntax_errors=n_syntax_errors,
            heuristic_chunk_count=heuristic_chunk_count,
            parse_elapsed_s=parse_elapsed,
            embed_elapsed_s=embed_elapsed,
            db_elapsed_s=db_elapsed,
            n_symbols=n_symbols,
            n_symbols_test=n_symbols_test,
            n_edges=n_edges,
            edge_stats=graph_stats,
            n_chunks_linked=n_linked,
            graph_elapsed_s=graph_elapsed,
        )
