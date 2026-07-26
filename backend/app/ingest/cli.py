"""Ingest CLI: clone a GitHub repo, parse it, inspect chunks, and (``--db``)
embed and store them in Postgres.

    python -m app.ingest.cli <github_url> [--dump PATH] [--sample N]
    python -m app.ingest.cli <github_url> --db

Without ``--db`` this is the Phase 1 inspect-only path (no model, no database).
With ``--db`` it runs the Phase 2 synchronous front-run of the ingest job
(SPEC §10 concept): upsert the repo, delete-and-replace its files/chunks, embed
with the real model tokenizer, and store everything. The full async ARQ job
lifecycle lands in Phase 4.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import logging
import random
import sys
import time
from collections import Counter
from pathlib import Path
from uuid import UUID

import asyncpg

from app.config import JEDI_FILE_TIMEOUT_S, PROGRESS_EVERY_N, get_settings
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
# of PROGRESS_EVERY_N (the print cadence); purely an encode/insert granularity.
EMBED_BATCH = 64


@dataclasses.dataclass
class IngestResult:
    """Everything the CLI needs to report and dump."""

    name: str
    head_sha: str
    default_branch: str
    selection: SelectionResult
    chunks: list[Chunk]
    n_syntax_errors: int
    elapsed_s: float


def ingest(url: str) -> IngestResult:
    """Run the Phase 1 pipeline for ``url`` and return chunks + stats."""
    counter = HeuristicTokenCounter()
    start = time.perf_counter()
    with cloned_repo(url) as info:
        selection = select_files(info.path)
        chunks: list[Chunk] = []
        n_syntax_errors = 0
        for source in selection.files:
            parsed = parse_file(source)
            if parsed is None:
                n_syntax_errors += 1
                continue
            chunks.extend(chunk_file(parsed, counter))
        elapsed = time.perf_counter() - start
        return IngestResult(
            name=info.name,
            head_sha=info.head_sha,
            default_branch=info.default_branch,
            selection=selection,
            chunks=chunks,
            n_syntax_errors=n_syntax_errors,
            elapsed_s=elapsed,
        )


@dataclasses.dataclass
class DbIngestResult:
    """Stats for a ``--db`` ingest, extending the Phase 1 block with DB/embed
    timings and the heuristic-vs-real chunk-count delta (Reconciliation 2)."""

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
    # Phase 3 symbol pass (SPEC §6). None when --no-graph.
    n_symbols: int = 0
    n_symbols_test: int = 0
    n_edges: int = 0
    edge_stats: EdgeStats | None = None
    n_chunks_linked: int = 0
    graph_elapsed_s: float = 0.0


def _parse_all(selection: SelectionResult) -> tuple[list[ParsedFile], int]:
    """Parse every selected file; return parsed files and the syntax-error count.

    Files that fail to parse are still stored in ``files`` (they power the code
    viewer); they simply contribute no chunks.
    """
    parsed: list[ParsedFile] = []
    n_syntax_errors = 0
    for source in selection.files:
        pf = parse_file(source)
        if pf is None:
            n_syntax_errors += 1
            continue
        parsed.append(pf)
    return parsed, n_syntax_errors


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


async def _build_graph(
    conn: asyncpg.Connection,
    repo_id: UUID,
    repo_dir: Path,
    selection: SelectionResult,
    parsed: list[ParsedFile],
) -> tuple[int, int, int, EdgeStats, int]:
    """Extract and store the symbol graph (SPEC §6.1).

    Must run **inside** the clone context: Jedi resolves against real files on
    disk, and the workdir is deleted when the context exits.

    Edges whose endpoints didn't survive symbol insertion are dropped rather
    than errored — a resolution can land on a line no chunk covers (a module
    docstring, a bare assignment), and that is a miss, not a failure.
    """
    symbols = extract_symbols(parsed)
    edges, stats = extract_edges(repo_dir, selection.files, symbols)

    await queries.clear_repo_graph(conn, repo_id)
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
    linked = await queries.backfill_chunk_symbol_ids(conn, repo_id)

    n_test = sum(1 for s in symbols if s.is_test)
    return len(symbols), n_test, len(edge_rows), stats, linked


async def ingest_to_db(url: str, *, build_graph: bool = True) -> DbIngestResult:
    """Clone, parse, embed, and store ``url`` in Postgres (delete-and-replace).

    The embedder's real tokenizer drives oversize-split decisions here, so the
    stored chunk count can differ from Phase 1's heuristic count — both are
    reported in the stats block.
    """
    settings = get_settings()
    embedder = get_embedder()  # doubles as the real TokenCounter (Reconciliation 2)

    parse_start = time.perf_counter()
    with cloned_repo(url) as info:
        selection = select_files(info.path)
        parsed, n_syntax_errors = _parse_all(selection)

        chunks: list[Chunk] = []
        for pf in parsed:
            chunks.extend(chunk_file(pf, embedder))
        # Same chunker, heuristic counter — count only, to report the delta.
        heuristic = HeuristicTokenCounter()
        heuristic_chunk_count = sum(len(chunk_file(pf, heuristic)) for pf in parsed)
        parse_elapsed = time.perf_counter() - parse_start

        file_rows = [(f.path, f.text, f.n_lines) for f in selection.files]

        embed_elapsed = 0.0
        db_start = time.perf_counter()
        pool = await create_pool(settings.DATABASE_URL)
        try:
            async with pool.acquire() as conn:
                repo_id = await queries.upsert_repo(
                    conn,
                    url=url,
                    name=info.name,
                    head_sha=info.head_sha,
                    default_branch=info.default_branch,
                )
                await queries.clear_repo_content(conn, repo_id)
                await queries.insert_files(conn, repo_id, file_rows)

                total = len(chunks)
                done = 0
                last_print = 0
                embed_start = time.perf_counter()
                for i in range(0, total, EMBED_BATCH):
                    batch = chunks[i : i + EMBED_BATCH]
                    vectors = embedder.encode([c.text for c in batch])
                    rows = [
                        _chunk_to_row(c, v)
                        for c, v in zip(batch, vectors, strict=True)
                    ]
                    await queries.insert_chunks(conn, repo_id, rows)
                    done += len(batch)
                    if done - last_print >= PROGRESS_EVERY_N or done == total:
                        rate = done / max(time.perf_counter() - embed_start, 1e-9)
                        print(f"  embedded {done}/{total} chunks ({rate:.0f}/s)")
                        last_print = done
                embed_elapsed = time.perf_counter() - embed_start

                # Symbol graph (SPEC §6.1) — inside the clone context, because
                # Jedi resolves against files on disk and `info.path` is
                # deleted when the context exits.
                n_symbols = n_symbols_test = n_edges = n_linked = 0
                graph_stats: EdgeStats | None = None
                graph_elapsed = 0.0
                if build_graph:
                    graph_start = time.perf_counter()
                    print("  building symbol graph (tree-sitter sites, Jedi resolve)…")
                    (
                        n_symbols,
                        n_symbols_test,
                        n_edges,
                        graph_stats,
                        n_linked,
                    ) = await _build_graph(conn, repo_id, info.path, selection, parsed)
                    graph_elapsed = time.perf_counter() - graph_start
                    print(
                        f"  symbols {n_symbols} ({n_symbols - n_symbols_test} impl / "
                        f"{n_symbols_test} test), edges {n_edges}, "
                        f"chunks linked {n_linked} ({graph_elapsed:.0f}s)"
                    )

                await queries.finalize_repo(
                    conn,
                    repo_id,
                    files_total=len(file_rows),
                    files_parsed=len(parsed),
                    chunks_total=total,
                    status="ready",
                )
        finally:
            await close_pool(pool)
        db_elapsed = time.perf_counter() - db_start - embed_elapsed - graph_elapsed

        return DbIngestResult(
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


def format_db_stats(result: DbIngestResult) -> str:
    sel = result.selection
    by_kind = Counter(c.kind for c in result.chunks)
    oversize = sum(1 for c in result.chunks if c.part == 1 and c.n_parts > 1)
    extra_parts = sum(c.n_parts - 1 for c in result.chunks if c.part == 1)
    n = len(result.chunks)
    delta = n - result.heuristic_chunk_count
    rate = n / max(result.embed_elapsed_s, 1e-9)
    lines = [
        "=" * 60,
        f"repo:        {result.name}  [DB ingest]",
        f"repo_id:     {result.repo_id}",
        f"head_sha:    {result.head_sha}",
        f"branch:      {result.default_branch}",
        "-" * 60,
        f"candidates:  {sel.n_candidates}",
        f"stored files:{sel.n_kept}  (parsed ok={sel.n_kept - result.n_syntax_errors}, "
        f"syntax-error={result.n_syntax_errors})",
        f"skipped:     non-python={sel.skipped_non_python} "
        f"ignored-dir={sel.skipped_ignored_dir} "
        f"too-large={sel.skipped_too_large} "
        f"binary={sel.skipped_binary} "
        f"decode-error={sel.skipped_decode_error}",
        "-" * 60,
        f"chunks:      {n} total  (real tokenizer)",
        f"  by kind:   module={by_kind['module']} class={by_kind['class']} "
        f"function={by_kind['function']} method={by_kind['method']}",
        f"  oversize:  {oversize} chunk(s) split into {extra_parts} extra part(s)",
        f"  vs heuristic: {result.heuristic_chunk_count} -> {n} "
        f"({delta:+d} from real token_len)",
        "-" * 60,
    ]
    st = result.edge_stats
    if st is not None:
        impl = result.n_symbols - result.n_symbols_test
        lines += [
            f"symbols:     {result.n_symbols} total  "
            f"({impl} implementation / {result.n_symbols_test} test)",
            f"edges:       {result.n_edges} stored",
        ]
        for kind in ("imports", "calls", "extends"):
            seen = st.sites.get(kind, 0)
            got = st.resolved.get(kind, 0)
            if seen:
                lines.append(
                    f"  {kind:<9}{got}/{seen} resolved "
                    f"({st.unresolved_rate(kind) * 100:.0f}% unresolved)"
                )
        lines += [
            f"  overall:  {st.unresolved_rate() * 100:.0f}% unresolved "
            f"(~20% expected, SPEC §6.1)",
            f"  timeouts: {len(st.timed_out_files)} file(s) hit the "
            f"{JEDI_FILE_TIMEOUT_S}s budget",
            f"chunks linked: {result.n_chunks_linked} to a symbol",
            "-" * 60,
        ]
    lines += [
        f"parse+chunk: {result.parse_elapsed_s:.2f}s",
        f"embed:       {result.embed_elapsed_s:.2f}s  ({rate:.0f} chunks/s)",
        f"symbol pass: {result.graph_elapsed_s:.2f}s",
        f"db write:    {result.db_elapsed_s:.2f}s",
        "=" * 60,
    ]
    return "\n".join(lines)


def _chunk_dict(chunk: Chunk) -> dict[str, object]:
    return {
        "file_path": chunk.file_path,
        "symbol": chunk.symbol,
        "kind": chunk.kind,
        "part": chunk.part,
        "n_parts": chunk.n_parts,
        "start_line": chunk.start_line,
        "end_line": chunk.end_line,
        "header": chunk.header,
        "code": chunk.code,
    }


def write_jsonl(chunks: list[Chunk], path: Path) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for chunk in chunks:
            fh.write(json.dumps(_chunk_dict(chunk), ensure_ascii=False) + "\n")


def format_stats(result: IngestResult) -> str:
    sel = result.selection
    by_kind = Counter(c.kind for c in result.chunks)
    oversize = sum(1 for c in result.chunks if c.part == 1 and c.n_parts > 1)
    extra_parts = sum(c.n_parts - 1 for c in result.chunks if c.part == 1)
    lines = [
        "=" * 60,
        f"repo:        {result.name}",
        f"head_sha:    {result.head_sha}",
        f"branch:      {result.default_branch}",
        "-" * 60,
        f"candidates:  {sel.n_candidates}",
        f"kept:        {sel.n_kept}",
        f"skipped:     non-python={sel.skipped_non_python} "
        f"ignored-dir={sel.skipped_ignored_dir} "
        f"too-large={sel.skipped_too_large} "
        f"binary={sel.skipped_binary} "
        f"decode-error={sel.skipped_decode_error} "
        f"syntax-error={result.n_syntax_errors}",
        "-" * 60,
        f"chunks:      {len(result.chunks)} total",
        f"  by kind:   module={by_kind['module']} class={by_kind['class']} "
        f"function={by_kind['function']} method={by_kind['method']}",
        f"  oversize:  {oversize} chunk(s) split into {extra_parts} extra part(s)",
        "-" * 60,
        f"elapsed:     {result.elapsed_s:.2f}s",
        "=" * 60,
    ]
    return "\n".join(lines)


def print_sample(chunks: list[Chunk], n: int, seed: int = 1234) -> None:
    if not chunks:
        print("(no chunks to sample)")
        return
    rng = random.Random(seed)
    picked = rng.sample(chunks, min(n, len(chunks)))
    for i, chunk in enumerate(picked, start=1):
        print(f"\n----- sample {i}/{len(picked)} "
              f"[{chunk.file_path}:{chunk.start_line}-{chunk.end_line}] -----")
        print(chunk.text)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.ingest.cli",
        description="Clone a public GitHub repo and inspect its code chunks.",
    )
    parser.add_argument("github_url", help="https://github.com/owner/repo")
    parser.add_argument(
        "--db",
        action="store_true",
        help="embed and store chunks in Postgres (delete-and-replace)",
    )
    parser.add_argument(
        "--no-graph",
        action="store_true",
        help="skip the Phase 3 symbol pass (--db only); chunks still stored",
    )
    parser.add_argument(
        "--dump", metavar="PATH", help="write all chunks as JSONL to PATH"
    )
    parser.add_argument(
        "--sample",
        type=int,
        metavar="N",
        help="print N random full chunks (seeded, reproducible)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    args = build_parser().parse_args(argv)

    if args.db:
        try:
            db_result = asyncio.run(
                ingest_to_db(args.github_url, build_graph=not args.no_graph)
            )
        except IngestError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(format_db_stats(db_result))
        if args.dump:
            dump_path = Path(args.dump)
            write_jsonl(db_result.chunks, dump_path)
            print(f"\nwrote {len(db_result.chunks)} chunks to {dump_path}")
        if args.sample:
            print_sample(db_result.chunks, args.sample)
        return 0

    try:
        result = ingest(args.github_url)
    except IngestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(format_stats(result))

    if args.dump:
        dump_path = Path(args.dump)
        write_jsonl(result.chunks, dump_path)
        print(f"\nwrote {len(result.chunks)} chunks to {dump_path}")

    if args.sample:
        print_sample(result.chunks, args.sample)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
