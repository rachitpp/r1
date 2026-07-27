"""Ingest CLI: clone a GitHub repo, parse it, inspect chunks, and (``--db``)
embed and store them in Postgres.

    python -m app.ingest.cli <github_url> [--dump PATH] [--sample N]
    python -m app.ingest.cli <github_url> --db

Without ``--db`` this is the Phase 1 inspect-only path (no model, no database).
With ``--db`` it runs :func:`app.ingest.pipeline.run_ingest` — the same function
the ARQ task calls (Phase 4 Reconciliation 2) — synchronously in the foreground,
and prints the stats block. The CLI's job is argument parsing and reporting; the
pipeline itself lives in one place so the queue and the CLI cannot diverge.
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

from app.config import JEDI_FILE_TIMEOUT_S, get_settings
from app.db import queries
from app.db.pool import close_pool, create_pool
from app.exceptions import IngestError
from app.ingest.chunker import Chunk, chunk_file
from app.ingest.clone import cloned_repo, repo_name_from_url
from app.ingest.filters import SelectionResult, select_files
from app.ingest.naive import naive_chunk_file
from app.ingest.parser import parse_file
from app.ingest.pipeline import STRATEGIES, IngestStats, baseline_url, run_ingest
from app.ingest.tokens import HeuristicTokenCounter
from app.logging_setup import configure_logging

logger = logging.getLogger(__name__)


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


def ingest(url: str, *, strategy: str = "ast") -> IngestResult:
    """Run the Phase 1 pipeline for ``url`` and return chunks + stats.

    ``strategy="naive"`` swaps in the §2.7 baseline chunker so chunk counts can
    be compared without touching a database.
    """
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
            if strategy == "naive":
                chunks.extend(naive_chunk_file(source))
            else:
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


async def ingest_to_db(
    url: str, *, build_graph: bool = True, strategy: str = "ast"
) -> IngestStats:
    """Create (or reuse) the repo row for ``url`` and run the pipeline inline.

    The foreground twin of the ARQ task: same :func:`run_ingest`, same status
    transitions and progress writes, just with the stats printed instead of a
    job result. Accepts any git-cloneable URL — GitHub validation belongs to
    ``POST /repos`` (§8), not to a developer's local CLI.

    ``strategy="naive"`` stores the baseline corpus under its own repo row
    (``<url>#naive``, name ``<name>@naive``) so it coexists with — and cannot
    clobber — the AST corpus at the pinned SHA. It also forces ``build_graph``
    off: the symbol graph is an AST product and would not be a baseline.
    """
    settings = get_settings()
    row_url = baseline_url(url) if strategy == "naive" else url
    name = repo_name_from_url(url)
    row_name = f"{name}@naive" if strategy == "naive" else name
    pool = await create_pool(settings.DATABASE_URL)
    try:
        async with pool.acquire() as conn:
            repo_id, _created = await queries.create_repo(
                conn, url=row_url, name=row_name
            )
        return await run_ingest(
            repo_id,
            pool=pool,
            build_graph=build_graph and strategy != "naive",
            strategy=strategy,
            log=lambda m: print(f"  {m}"),
        )
    finally:
        await close_pool(pool)


def format_db_stats(result: IngestStats) -> str:
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
        lines.append(
            "  site outcomes  (resolved / external-dropped / unmapped / failed)"
        )
        for kind in ("imports", "calls", "extends"):
            seen = st.sites.get(kind, 0)
            if not seen:
                continue
            lines.append(
                f"  {kind:<9}{seen:>5} sites: "
                f"{st.resolved.get(kind, 0)} / {st.out_of_repo.get(kind, 0)} / "
                f"{st.unmapped.get(kind, 0)} / {st.no_target.get(kind, 0)}"
                f"   fail {st.failure_rate(kind) * 100:.0f}%"
            )
        lines += [
            f"  overall:  fail {st.failure_rate() * 100:.0f}% "
            f"(~20% budget, SPEC §6.1) · "
            f"external-dropped {st.out_of_repo_rate() * 100:.0f}%",
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
        "--strategy",
        choices=STRATEGIES,
        default="ast",
        help="chunk boundaries: 'ast' (the product) or 'naive' (SPEC §2.7 "
        "measurement baseline — fixed character windows, own repo row, no "
        "symbol graph). Baseline only; never the product path.",
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
    configure_logging()
    args = build_parser().parse_args(argv)

    if args.db:
        try:
            db_result = asyncio.run(
                ingest_to_db(
                    args.github_url,
                    build_graph=not args.no_graph,
                    strategy=args.strategy,
                )
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
        result = ingest(args.github_url, strategy=args.strategy)
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
