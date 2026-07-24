"""Phase 1 CLI: clone a GitHub repo, parse it, and inspect the chunks.

    python -m app.ingest.cli <github_url> [--dump PATH] [--sample N]

No database, embeddings, HTTP, or agent code — the CLI is the only interface
in Phase 1 (SPEC §2). It prints a stats block and can dump chunks as JSONL or
print a seeded random sample for eyeball spot-checks.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import random
import sys
import time
from collections import Counter
from pathlib import Path

from app.exceptions import IngestError
from app.ingest.chunker import Chunk, chunk_file
from app.ingest.clone import cloned_repo
from app.ingest.filters import SelectionResult, select_files
from app.ingest.parser import parse_file
from app.ingest.tokens import HeuristicTokenCounter

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
