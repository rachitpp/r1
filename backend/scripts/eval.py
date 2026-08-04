"""Retrieval evaluation against the frozen EVAL.md ground truth (SPEC §11).

Runs each benchmark question through one or more retrieval modes and reports
hit@5 / hit@10, then appends a dated results block to docs/EVAL.md. Never edits
an existing block, never tunes against individual questions — it only measures.

    uv run python scripts/eval.py --mode all
    uv run python scripts/eval.py --mode hybrid+rerank
    uv run python scripts/eval.py --mode all --include-tests   # shadowed condition

``--include-tests`` restores test chunks to the candidate pool (SPEC §5.4). The
default condition excludes them; running both is what keeps the counterfactual
measurable rather than asserted.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import datetime as dt
import re
import sys
from pathlib import Path
from uuid import UUID

import asyncpg
import yaml

# scripts/ is a sibling of app/, not a package — put backend/ on the path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.db.pool import close_pool, create_pool  # noqa: E402
from app.db.queries import resolve_snapshot_id  # noqa: E402
from app.retrieval.hybrid import (  # noqa: E402
    MODES,
    Mode,
    ProsePolicy,
    SearchHit,
    qualname_matches,
    search,
)

DOCS = Path(__file__).resolve().parent.parent.parent / "docs"
EVAL_MD = DOCS / "EVAL.md"  # the default benchmark; --benchmark selects another
KS = (3, 5, 10)  # hit@3 shows reranker precision; hit@10 the recall gate
MAX_K = max(KS)


def _extract_yaml_block(text: str, source: Path) -> str:
    """Return the first ```yaml fenced block's body from ``text``."""
    match = re.search(r"```yaml\n(.*?)\n```", text, re.DOTALL)
    if match is None:
        raise SystemExit(f"{source.name}: no ```yaml question block found")
    return match.group(1)


def _parse_eval_md(benchmark: Path) -> tuple[str, str | None, list[dict]]:
    """Return ``(benchmark_url, pinned_sha, questions)`` parsed from ``benchmark``.

    Any file in the EVAL.md format works, which is what lets a second repo be
    measured without the first one's ground truth being touched. Each benchmark
    owns its own results blocks, so the append-only rule stays per-file.
    """
    if not benchmark.is_file():
        raise SystemExit(f"benchmark file not found: {benchmark}")
    text = benchmark.read_text(encoding="utf-8")
    url_match = re.search(r"\((https://github\.com/[^)]+)\)", text)
    sha_match = re.search(r"Pinned commit:\*\*\s*`([0-9a-f]{7,40})`", text)
    questions = yaml.safe_load(_extract_yaml_block(text, benchmark))
    if not isinstance(questions, list):
        raise SystemExit(f"{benchmark.name}: question block did not parse to a list")
    if url_match is None:
        raise SystemExit(f"{benchmark.name}: no benchmark repo URL found")
    return url_match.group(1), (sha_match.group(1) if sha_match else None), questions


def _first_hit_rank(
    hits: list[SearchHit], files: set[str], symbols: list[str]
) -> int | None:
    """1-based rank of the first hit satisfying file/symbol ground truth, else None.

    One rank drives every metric: hit@k = ``rank <= k``; reciprocal rank = ``1/rank``
    (0 on a miss), whose mean over questions is MRR. A reranker's job is to make
    this rank small, which hit@3 / MRR reward and hit@10 barely reflects.
    """
    for i, h in enumerate(hits, start=1):
        if h["file_path"] in files or any(
            qualname_matches(h["symbol"], s) for s in symbols
        ):
            return i
    return None


async def _truth_file_guard(
    conn: asyncpg.Connection, snapshot_id: UUID, questions: list[dict]
) -> None:
    """Warn loudly if any ground-truth file is missing from the files table.

    Catches path-format drift (Reconciliation 3) before it silently zeroes a
    question — a missing file can never be a hit.
    """
    rows = await conn.fetch("SELECT path FROM files WHERE snapshot_id = $1", snapshot_id)
    present = {r["path"] for r in rows}
    wanted = {
        f for q in questions for f in q.get("truth", {}).get("files", [])
    }
    missing = sorted(wanted - present)
    if missing:
        print("!" * 60)
        print(f"WARNING: {len(missing)} ground-truth file(s) absent from `files`:")
        for path in missing:
            print(f"  - {path}")
        print("These questions cannot hit on file match — check for path drift.")
        print("!" * 60)


@dataclasses.dataclass
class ConditionResult:
    """Measurements for one corpus condition (SPEC §5.4).

    ``label`` names the condition in the report and the EVAL.md block, so a
    reader can never confuse an implementation-only run with a shadowed one.
    """

    label: str
    include_tests: bool
    hits: dict[Mode, dict[int, int]]
    rr_sum: dict[Mode, float]
    grid: dict[str, dict[Mode, bool]]


CONDITION_LABELS = {
    False: "implementation-only (default, is_test excluded)",
    True: "shadowed (--include-tests, is_test included)",
}


async def _measure(
    conn: asyncpg.Connection,
    snapshot_id: UUID,
    questions: list[dict],
    modes: list[Mode],
    *,
    include_tests: bool,
    prose: ProsePolicy = "exclude",
) -> ConditionResult:
    """Run every question through every mode under one corpus condition."""
    # hits[mode][k] = #questions hitting within top-k; rr_sum -> MRR.
    hits: dict[Mode, dict[int, int]] = {m: {k: 0 for k in KS} for m in modes}
    rr_sum: dict[Mode, float] = {m: 0.0 for m in modes}
    grid: dict[str, dict[Mode, bool]] = {}
    for q in questions:
        truth = q.get("truth", {})
        files = set(truth.get("files", []))
        symbols = list(truth.get("symbols", []))
        grid[q["id"]] = {}
        for mode in modes:
            found = await search(
                conn,
                snapshot_id,
                q["question"],
                k=MAX_K,
                mode=mode,
                include_tests=include_tests,
                prose=prose,
            )
            rank = _first_hit_rank(found, files, symbols)
            for k in KS:
                if rank is not None and rank <= k:
                    hits[mode][k] += 1
            rr_sum[mode] += 1.0 / rank if rank is not None else 0.0
            grid[q["id"]][mode] = rank is not None and rank <= MAX_K
    return ConditionResult(
        label=CONDITION_LABELS[include_tests],
        include_tests=include_tests,
        hits=hits,
        rr_sum=rr_sum,
        grid=grid,
    )


async def run(
    modes: list[Mode],
    repo_ref: str,
    conditions: list[bool],
    benchmark: Path = EVAL_MD,
    prose: ProsePolicy = "exclude",
) -> int:
    url, sha, questions = _parse_eval_md(benchmark)
    ref = repo_ref or url
    settings = get_settings()
    pool = await create_pool(settings.DATABASE_URL)
    results: list[ConditionResult] = []
    try:
        async with pool.acquire() as conn:
            snapshot_id = await resolve_snapshot_id(conn, ref)
            if snapshot_id is None:
                print(f"error: repo {ref!r} not ingested; run the CLI --db first")
                return 1

            # `repo_snapshots.commit_sha`, not `repos.head_sha`. V2 stopped
            # writing the old table, so this returned NULL for every post-007
            # ingest and the pinned-SHA warning below could never fire — the one
            # check standing between a result block and the wrong commit.
            head_sha = await conn.fetchval(
                "SELECT commit_sha FROM repo_snapshots WHERE id = $1", snapshot_id
            )
            n_chunks = await conn.fetchval(
                "SELECT count(*) FROM chunks WHERE snapshot_id = $1", snapshot_id
            )
            # `NOT is_prose` as well as `NOT is_test`, since §30: prose chunks
            # are not test chunks, so without it every README section counts as
            # implementation and the corpus line overstates the pool these
            # numbers were measured against. Exactly the trap §30.7 predicted
            # for the CLAUDE.md verification query, in a second place.
            n_impl = await conn.fetchval(
                "SELECT count(*) FROM chunks WHERE snapshot_id = $1 "
                "AND NOT is_test AND NOT is_prose",
                snapshot_id,
            )
            n_prose = await conn.fetchval(
                "SELECT count(*) FROM chunks WHERE snapshot_id = $1 AND is_prose",
                snapshot_id,
            )
            if sha and head_sha and not head_sha.startswith(sha[:12]):
                print(f"WARNING: ingested head {head_sha} != EVAL pinned {sha}")

            await _truth_file_guard(conn, snapshot_id, questions)

            for include_tests in conditions:
                label = CONDITION_LABELS[include_tests]
                if prose == "include":
                    label += " +prose"
                print(f"\n>>> condition: {label}")
                results.append(
                    await _measure(
                        conn,
                        snapshot_id,
                        questions,
                        modes,
                        include_tests=include_tests,
                        prose=prose,
                    )
                )
    finally:
        await close_pool(pool)

    total = len(questions)
    corpus = _corpus_line(n_chunks, n_impl, n_prose)
    print(_format_report(modes, results, url, head_sha, corpus, total))
    _append_results(modes, results, url, head_sha, corpus, total, benchmark)
    print(f"\nappended results block to {benchmark}")
    return 0


def _corpus_line(n_chunks: int, n_impl: int, n_prose: int = 0) -> str:
    """Chunk counts by class — the corpus the numbers came from.

    Three-way since §30. Prose is reported *and* named as excluded, because the
    interesting fact about a §30 corpus is not that it contains documentation
    but that the measured pool does not.
    """
    n_test = n_chunks - n_impl - n_prose
    line = f"{n_chunks} chunks ({n_impl} implementation, {n_test} test"
    if n_prose:
        line += f", {n_prose} prose/config excluded"
    return line + ")"


def _rate(n: int, total: int) -> str:
    return f"{n / total:.2f} ({n}/{total})"


def _summary_table(
    modes: list[Mode],
    hits: dict[Mode, dict[int, int]],
    rr_sum: dict[Mode, float],
    total: int,
) -> list[str]:
    lines = [
        "| Mode | hit@3 | hit@5 | hit@10 | MRR |",
        "|---|---|---|---|---|",
    ]
    for mode in modes:
        mrr = rr_sum[mode] / total if total else 0.0
        lines.append(
            f"| {mode} | {_rate(hits[mode][3], total)} | "
            f"{_rate(hits[mode][5], total)} | {_rate(hits[mode][10], total)} | "
            f"{mrr:.3f} |"
        )
    return lines


def _grid_table(
    modes: list[Mode], grid: dict[str, dict[Mode, bool]]
) -> list[str]:
    header = "| q | " + " | ".join(modes) + " |"
    sep = "|---|" + "|".join("---" for _ in modes) + "|"
    lines = [header, sep]
    for qid in sorted(grid):
        cells = " | ".join("✓" if grid[qid][m] else "·" for m in modes)
        lines.append(f"| {qid} | {cells} |")
    return lines


def _format_report(
    modes: list[Mode],
    results: list[ConditionResult],
    url: str,
    head_sha: str | None,
    corpus: str,
    total: int,
) -> str:
    sha_short = (head_sha or "?")[:12]
    lines = [
        "=" * 60,
        f"eval: {url} @ {sha_short}  ({corpus}, {total} questions)",
        "=" * 60,
    ]
    for res in results:
        lines += [
            "",
            f"--- {res.label} ---",
            "",
            "hit@k / MRR summary:",
            *_summary_table(modes, res.hits, res.rr_sum, total),
            "",
            "per-question hit@10:",
            *_grid_table(modes, res.grid),
        ]
    return "\n".join(lines)


def _append_results(
    modes: list[Mode],
    results: list[ConditionResult],
    url: str,
    head_sha: str | None,
    corpus: str,
    total: int,
    benchmark: Path = EVAL_MD,
) -> None:
    """Append ONE dated block covering every measured condition.

    EVAL.md is frozen (CLAUDE.md): blocks are only ever appended, never edited,
    and each condition is labelled so runs can never be silently conflated.
    """
    today = dt.date.today().isoformat()
    sha_short = (head_sha or "?")[:12]
    block = [
        "",
        f"### Results — {today}",
        "",
        f"**Repo:** {url} @ `{sha_short}` — {corpus}, "
        f"{total} questions. Modes: {', '.join(modes)}.",
        "",
    ]
    for res in results:
        block += [
            f"**Corpus condition:** {res.label}",
            "",
            *_summary_table(modes, res.hits, res.rr_sum, total),
            "",
            "Per-question hit@10:",
            "",
            *_grid_table(modes, res.grid),
            "",
        ]
    with benchmark.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(block) + "\n")


def main(argv: list[str] | None = None) -> int:
    # The report uses ✓/· and Windows consoles default to cp1252, which cannot
    # encode either. That crashed *after* a full measurement had been computed
    # and before the results block was appended — the run's entire cost lost to
    # a print. Reconfiguring is cheaper than remembering to set PYTHONIOENCODING.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

    parser = argparse.ArgumentParser(prog="scripts/eval.py")
    parser.add_argument(
        "--mode",
        default="all",
        help="'all', one mode, or a comma list (e.g. vector,fts,hybrid). "
        f"Modes: {', '.join(MODES)}.",
    )
    parser.add_argument(
        "--repo",
        default="",
        help="repo url or id (default: the repo named in the benchmark file)",
    )
    parser.add_argument(
        "--benchmark",
        default=str(EVAL_MD),
        metavar="PATH",
        help="benchmark file in the EVAL.md format (default: docs/EVAL.md). "
        "Results append to whichever file is used, so each benchmark keeps its "
        "own append-only history and neither can overwrite the other's.",
    )
    condition = parser.add_mutually_exclusive_group()
    condition.add_argument(
        "--include-tests",
        action="store_true",
        help="measure the shadowed condition: keep is_test chunks in the pool "
        "(SPEC §5.4). Default excludes them.",
    )
    condition.add_argument(
        "--both-conditions",
        action="store_true",
        help="measure default AND --include-tests, appending one labelled block.",
    )
    parser.add_argument(
        "--include-prose",
        action="store_true",
        help="blend §30 prose/config chunks into the candidate pool. The default "
        "excludes them (SPEC §30.4), and this flag is what keeps that exclusion a "
        "measured decision rather than an asserted one: run it to see how far "
        "documentation outranks implementation when nothing holds it back.",
    )
    args = parser.parse_args(argv)
    if args.mode == "all":
        modes: list[Mode] = list(MODES)
    else:
        requested = [m.strip() for m in args.mode.split(",") if m.strip()]
        unknown = [m for m in requested if m not in MODES]
        if unknown:
            parser.error(f"unknown mode(s): {unknown}; valid: {list(MODES)}")
        modes = [m for m in MODES if m in requested]  # canonical order, deduped
    if args.both_conditions:
        conditions = [False, True]
    else:
        conditions = [bool(args.include_tests)]
    prose: ProsePolicy = "include" if args.include_prose else "exclude"
    return asyncio.run(
        run(modes, args.repo, conditions, Path(args.benchmark), prose=prose)
    )


if __name__ == "__main__":
    raise SystemExit(main())
