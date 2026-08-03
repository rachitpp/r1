"""Ingest CLI: clone a GitHub repo, parse it, inspect chunks, and (``--db``)
embed and store them in Postgres.

    python -m app.ingest.cli <github_url> [--dump PATH] [--sample N]
    python -m app.ingest.cli <github_url> --db
    python -m app.ingest.cli <github_url> --db --json    # for scripts

Without ``--db`` this is the Phase 1 inspect-only path (no model, no database).
With ``--db`` it runs :func:`app.ingest.pipeline.run_ingest` — the same function
the ARQ task calls (Phase 4 Reconciliation 2) — synchronously in the foreground,
and prints the stats block. The CLI's job is argument parsing and reporting; the
pipeline itself lives in one place so the queue and the CLI cannot diverge.

**``--json`` makes stdout a contract.** Exactly one JSON object is written
there, on success *and* on failure, always carrying ``ok``; everything humans
read — progress lines, warnings, samples — is diverted to stderr. Without that
split the pipeline's own progress `print`s land in the middle of the document
and `| jq` fails on output that is otherwise correct. Exit status mirrors
``ok``, so a shell can branch without parsing anything.
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
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

from app.config import JEDI_FILE_TIMEOUT_S, get_settings
from app.db import queries
from app.db.pool import close_pool, create_pool
from app.exceptions import IngestError, SnapshotSuperseded
from app.ingest.chunker import Chunk, chunk_file
from app.ingest.clone import cloned_repo, repo_name_from_url
from app.ingest.filters import SelectionResult, select_files
from app.ingest.naive import naive_chunk_file
from app.ingest.parser import parse_file
from app.ingest.pipeline import STRATEGIES, IngestStats, run_ingest
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
    url: str,
    *,
    build_graph: bool = True,
    strategy: str = "ast",
    owner: str | None = None,
    rev: str | None = None,
    log: Callable[[str], None] | None = None,
) -> IngestStats:
    """Create (or reuse) the repo row for ``url`` and run the pipeline inline.

    The foreground twin of the ARQ task: same :func:`run_ingest`, same status
    transitions and progress writes, just with the stats printed instead of a
    job result. Accepts any git-cloneable URL — GitHub validation belongs to
    ``POST /repos`` (§8), not to a developer's local CLI.

    ``strategy="naive"`` stores the baseline corpus as its own snapshot of the
    same source (SPEC §14.6), so it coexists with — and cannot clobber — the AST
    corpus at the same commit. It also forces ``build_graph`` off: the symbol
    graph is an AST product and would not be a baseline.

    ``log`` receives the pipeline's progress lines. It is a parameter rather
    than a hardcoded ``print`` so ``--json`` can send them to stderr; a progress
    line on stdout would sit inside the JSON document.
    """
    emit = log if log is not None else (lambda m: print(f"  {m}"))
    settings = get_settings()
    name = repo_name_from_url(url)
    pool = await create_pool(settings.DATABASE_URL)
    try:
        async with pool.acquire() as conn:
            source_id = await queries.get_or_create_source(conn, url=url, name=name)
            snapshot_id = await queries.create_snapshot(conn, source_id, strategy=strategy)
            # Since V1 a repo is only reachable through a `user_repos` row
            # (SPEC §13.5): one written without an owner is invisible to
            # `GET /repos` and 404s on every route, for everyone. `POST /repos`
            # links the submitter; there is no submitter here, so resolve one.
            owner_id = await queries.resolve_owner_id(conn, owner)
            if owner_id is not None:
                await queries.link_user_repo(conn, owner_id, snapshot_id)
            else:
                # Loudly, not silently: the ingest still runs and the corpus is
                # still measurable from the CLI and the eval scripts, but the
                # repo will not appear in anyone's library until it is linked.
                print(
                    "  warning: no owner for this repo — it will not appear in "
                    "the web app.\n"
                    "           pass --owner <github-login>, or set "
                    "BOOTSTRAP_GITHUB_ID in backend/.env.",
                    file=sys.stderr,
                )
        return await run_ingest(
            snapshot_id,
            pool=pool,
            build_graph=build_graph and strategy != "naive",
            strategy=strategy,
            rev=rev,
            log=emit,
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
        f"snapshot_id: {result.snapshot_id}",
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


def print_sample(
    chunks: list[Chunk], n: int, seed: int = 1234, stream: TextIO | None = None
) -> None:
    out = stream if stream is not None else sys.stdout
    if not chunks:
        print("(no chunks to sample)", file=out)
        return
    rng = random.Random(seed)
    picked = rng.sample(chunks, min(n, len(chunks)))
    for i, chunk in enumerate(picked, start=1):
        print(f"\n----- sample {i}/{len(picked)} "
              f"[{chunk.file_path}:{chunk.start_line}-{chunk.end_line}] -----",
              file=out)
        print(chunk.text, file=out)


# --- machine-readable output (--json) --------------------------------------


def _selection_payload(sel: SelectionResult) -> dict[str, object]:
    return {
        "n_candidates": sel.n_candidates,
        "n_kept": sel.n_kept,
        "skipped": {
            "non_python": sel.skipped_non_python,
            "ignored_dir": sel.skipped_ignored_dir,
            "too_large": sel.skipped_too_large,
            "binary": sel.skipped_binary,
            "decode_error": sel.skipped_decode_error,
        },
    }


def _chunk_summary(chunks: list[Chunk]) -> dict[str, object]:
    by_kind = Counter(c.kind for c in chunks)
    return {
        "total": len(chunks),
        "by_kind": {
            kind: by_kind[kind] for kind in ("module", "class", "function", "method")
        },
        "oversize": sum(1 for c in chunks if c.part == 1 and c.n_parts > 1),
        "extra_parts": sum(c.n_parts - 1 for c in chunks if c.part == 1),
    }


def stats_payload(result: IngestResult) -> dict[str, object]:
    """Inspect-only run, as one JSON-serialisable object."""
    return {
        "ok": True,
        "mode": "inspect",
        "name": result.name,
        "head_sha": result.head_sha,
        "default_branch": result.default_branch,
        "selection": _selection_payload(result.selection),
        "n_syntax_errors": result.n_syntax_errors,
        "chunks": _chunk_summary(result.chunks),
        "elapsed_s": round(result.elapsed_s, 3),
    }


def db_stats_payload(result: IngestStats) -> dict[str, object]:
    """Stored run, as one JSON-serialisable object.

    Mirrors :func:`format_db_stats` field for field — including the edge
    outcomes, which are the numbers the §6.1 resolution budget is judged on and
    the reason a scripted ingest is worth reading at all.
    """
    payload: dict[str, object] = {
        "ok": True,
        "mode": "db",
        "name": result.name,
        "snapshot_id": result.snapshot_id,
        "head_sha": result.head_sha,
        "default_branch": result.default_branch,
        "selection": _selection_payload(result.selection),
        "n_syntax_errors": result.n_syntax_errors,
        "chunks": {
            **_chunk_summary(result.chunks),
            "heuristic_count": result.heuristic_chunk_count,
        },
        "timings_s": {
            "parse": round(result.parse_elapsed_s, 3),
            "embed": round(result.embed_elapsed_s, 3),
            "graph": round(result.graph_elapsed_s, 3),
            "db_write": round(result.db_elapsed_s, 3),
        },
    }
    st = result.edge_stats
    if st is not None:
        payload["graph"] = {
            "n_symbols": result.n_symbols,
            "n_symbols_test": result.n_symbols_test,
            "n_symbols_impl": result.n_symbols - result.n_symbols_test,
            "n_edges": result.n_edges,
            "n_chunks_linked": result.n_chunks_linked,
            "sites": {
                kind: {
                    "seen": st.sites.get(kind, 0),
                    "resolved": st.resolved.get(kind, 0),
                    "external_dropped": st.out_of_repo.get(kind, 0),
                    "unmapped": st.unmapped.get(kind, 0),
                    "failed": st.no_target.get(kind, 0),
                    "failure_rate": round(st.failure_rate(kind), 4),
                }
                for kind in ("imports", "calls", "extends")
                if st.sites.get(kind, 0)
            },
            "failure_rate": round(st.failure_rate(), 4),
            "external_dropped_rate": round(st.out_of_repo_rate(), 4),
            "timed_out_files": len(st.timed_out_files),
        }
    return payload


def _emit_json(payload: dict[str, object]) -> None:
    """The single JSON document, on stdout, with a trailing newline."""
    print(json.dumps(payload, indent=2))


def _fail_json(kind: str, message: str) -> int:
    _emit_json({"ok": False, "error": {"type": kind, "message": message}})
    return 1


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
        "--rev",
        metavar="SHA",
        help="ingest the repo at this commit instead of the branch tip. What "
        "makes `GET /repos/{id}/compare` (SPEC §28) usable: comparing a repo "
        "against its own past needs two snapshots at two commits, and without "
        "this every snapshot is pinned to whatever HEAD was on the day it ran. "
        "Must be within the most recent commits a shallow clone fetches.",
    )
    parser.add_argument(
        "--owner",
        metavar="LOGIN",
        help="GitHub login to put this repo in the library of (SPEC §13.5). "
        "Defaults to the BOOTSTRAP_GITHUB_ID user. Without either, the repo is "
        "ingested but belongs to nobody and stays invisible to the web app.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit one JSON object on stdout (success or failure) and send "
        "every human-readable line to stderr; exit status mirrors `ok`",
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
    as_json = bool(args.json)
    # In --json mode stdout belongs to the document; everything else is stderr.
    human: TextIO = sys.stderr if as_json else sys.stdout

    if args.db:
        try:
            db_result = asyncio.run(
                ingest_to_db(
                    args.github_url,
                    build_graph=not args.no_graph,
                    strategy=args.strategy,
                    owner=args.owner,
                    rev=args.rev,
                    log=lambda m: print(f"  {m}", file=human),
                )
            )
        except SnapshotSuperseded as dedup:
            # Not an error: this commit is already stored (SPEC §14.4). Exit 0
            # either way — a script re-running an ingest wants "the corpus is
            # there", and a nonzero status would make that look like a failure.
            if as_json:
                _emit_json(
                    {
                        "ok": True,
                        "mode": "db",
                        "deduplicated": True,
                        "snapshot_id": str(dedup.kept_id),
                    }
                )
            else:
                print(f"already ingested — reusing snapshot {dedup.kept_id}")
            return 0
        except IngestError as exc:
            if as_json:
                return _fail_json(type(exc).__name__, str(exc))
            print(f"error: {exc}", file=sys.stderr)
            return 1

        if args.dump:
            dump_path = Path(args.dump)
            write_jsonl(db_result.chunks, dump_path)
        if as_json:
            payload = db_stats_payload(db_result)
            if args.dump:
                payload["dump_path"] = str(Path(args.dump))
            _emit_json(payload)
        else:
            print(format_db_stats(db_result))
            if args.dump:
                print(f"\nwrote {len(db_result.chunks)} chunks to {Path(args.dump)}")
        if args.sample:
            print_sample(db_result.chunks, args.sample, stream=human)
        return 0

    try:
        result = ingest(args.github_url, strategy=args.strategy)
    except IngestError as exc:
        if as_json:
            return _fail_json(type(exc).__name__, str(exc))
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.dump:
        dump_path = Path(args.dump)
        write_jsonl(result.chunks, dump_path)
    if as_json:
        payload = stats_payload(result)
        if args.dump:
            payload["dump_path"] = str(Path(args.dump))
        _emit_json(payload)
    else:
        print(format_stats(result))
        if args.dump:
            print(f"\nwrote {len(result.chunks)} chunks to {Path(args.dump)}")

    if args.sample:
        print_sample(result.chunks, args.sample, stream=human)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
