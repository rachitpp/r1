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
from app.exceptions import IngestError
from app.ingest.chunker import Chunk, chunk_file
from app.ingest.clone import cloned_repo
from app.ingest.embedder import get_embedder
from app.ingest.filters import SelectionResult, is_test_path, select_files
from app.ingest.parser import ParsedFile, parse_file
from app.ingest.symbols import EdgeStats, extract_edges, extract_symbols
from app.ingest.tokens import HeuristicTokenCounter

logger = logging.getLogger(__name__)

# How many chunks to embed per model call before inserting a batch. Independent
# of PROGRESS_EVERY_N (the progress-write cadence); purely an encode/insert
# granularity.
EMBED_BATCH = 64

# Called with one human-readable line per pipeline milestone. The CLI prints
# these; the worker logs them.
ProgressLog = Callable[[str], None]


@dataclasses.dataclass
class IngestStats:
    """What one completed ingest produced.

    ``chunks`` is carried so the CLI can dump or sample the exact chunk set that
    was stored; the worker ignores it.
    """

    name: str
    repo_id: str
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
    repo_id: UUID,
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
        repo_id,
        [
            (s.name, s.qualname, s.kind, s.file_path, s.start_line, s.end_line, s.is_test)
            for s in symbols
        ],
    )
    id_of = await queries.symbol_id_map(conn, repo_id)
    edge_rows: list[queries.EdgeRowT] = [
        (id_of[e.from_key], id_of[e.to_key], e.kind, e.line)
        for e in edges
        if e.from_key in id_of and e.to_key in id_of
    ]
    await queries.insert_edges(conn, repo_id, edge_rows)

    n_test = sum(1 for s in symbols if s.is_test)
    return len(symbols), n_test, len(edge_rows), stats


async def run_ingest(
    repo_id: UUID,
    *,
    pool: asyncpg.Pool | None = None,
    build_graph: bool = True,
    log: ProgressLog | None = None,
) -> IngestStats:
    """Run the full pipeline for an existing ``repos`` row and store the result.

    The row must already exist (``POST /repos`` or the CLI creates it); this
    function owns everything after that, including the §10 status transitions and
    progress counters. Raises on failure with the row left mid-flight — the
    caller records ``failed`` (the worker) so the error text has one owner.

    ``pool`` lets a long-lived caller (the worker) share its pool; when omitted
    one is created and closed here.
    """
    say: ProgressLog = log if log is not None else (lambda _msg: None)
    own_pool = pool is None
    if pool is None:
        pool = await create_pool(get_settings().DATABASE_URL)
    try:
        async with pool.acquire() as conn:
            return await _run(conn, repo_id, build_graph=build_graph, say=say)
    finally:
        if own_pool:
            await close_pool(pool)


async def _run(
    conn: asyncpg.Connection,
    repo_id: UUID,
    *,
    build_graph: bool,
    say: ProgressLog,
) -> IngestStats:
    row = await conn.fetchrow("SELECT url FROM repos WHERE id = $1", repo_id)
    if row is None:
        raise IngestError(f"no repo row {repo_id}")
    url = str(row["url"])

    embedder = get_embedder()  # also the real TokenCounter for oversize splits
    await queries.start_ingest(conn, repo_id, status="cloning")
    say(f"cloning {url}")

    parse_start = time.perf_counter()
    with cloned_repo(url) as info:
        await queries.set_repo_clone_info(
            conn,
            repo_id,
            name=info.name,
            head_sha=info.head_sha,
            default_branch=info.default_branch,
        )

        # --- parsing -------------------------------------------------------
        await queries.set_repo_status(conn, repo_id, "parsing")
        selection = select_files(info.path)
        say(f"selected {selection.n_kept} of {selection.n_candidates} candidate files")

        # Delete-and-replace at the start (§10 idempotency): a retry or a
        # re-ingest must not stack rows on top of the previous attempt's.
        await queries.clear_repo_graph(conn, repo_id)
        await queries.clear_repo_content(conn, repo_id)
        file_rows = [(f.path, f.text, f.n_lines) for f in selection.files]
        await queries.insert_files(conn, repo_id, file_rows)
        await queries.set_repo_progress(
            conn, repo_id, files_total=len(file_rows), files_parsed=0
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
                await queries.set_repo_progress(conn, repo_id, files_parsed=i)
                last_written = i
        say(f"parsed {len(parsed)} files ({n_syntax_errors} syntax errors)")

        chunks: list[Chunk] = []
        for pf in parsed:
            chunks.extend(chunk_file(pf, embedder))
        # Same chunker, heuristic counter — count only, to report the delta
        # between the heuristic and the real tokenizer (Phase 2 Reconciliation 2).
        heuristic = HeuristicTokenCounter()
        heuristic_chunk_count = sum(len(chunk_file(pf, heuristic)) for pf in parsed)
        parse_elapsed = time.perf_counter() - parse_start
        await queries.set_repo_progress(
            conn, repo_id, chunks_total=len(chunks), chunks_embedded=0
        )

        # --- linking -------------------------------------------------------
        n_symbols = n_symbols_test = n_edges = 0
        graph_stats: EdgeStats | None = None
        graph_elapsed = 0.0
        if build_graph:
            await queries.set_repo_status(conn, repo_id, "linking")
            say("building symbol graph (tree-sitter sites, Jedi resolve)…")
            graph_start = time.perf_counter()
            n_symbols, n_symbols_test, n_edges, graph_stats = await _store_graph(
                conn, repo_id, info.path, selection, parsed
            )
            graph_elapsed = time.perf_counter() - graph_start
            say(
                f"symbols {n_symbols} ({n_symbols - n_symbols_test} impl / "
                f"{n_symbols_test} test), edges {n_edges} ({graph_elapsed:.0f}s)"
            )

        # --- embedding -----------------------------------------------------
        # The clone is no longer needed from here on, but staying inside the
        # context costs only disk and keeps the exit path single.
        await queries.set_repo_status(conn, repo_id, "embedding")
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
            await queries.insert_chunks(conn, repo_id, rows)
            done += len(batch)
            if done - last_written >= PROGRESS_EVERY_N or done == total:
                await queries.set_repo_progress(conn, repo_id, chunks_embedded=done)
                rate = done / max(time.perf_counter() - embed_start, 1e-9)
                say(f"embedded {done}/{total} chunks ({rate:.0f}/s)")
                last_written = done
        embed_elapsed = time.perf_counter() - embed_start

        n_linked = 0
        if build_graph:
            n_linked = await queries.backfill_chunk_symbol_ids(conn, repo_id)
            say(f"linked {n_linked} chunks to a symbol")

        await queries.finalize_repo(
            conn,
            repo_id,
            files_total=len(file_rows),
            files_parsed=len(parsed),
            chunks_total=total,
            status="ready",
        )
        db_elapsed = time.perf_counter() - db_start - embed_elapsed

        return IngestStats(
            name=info.name,
            repo_id=str(repo_id),
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
