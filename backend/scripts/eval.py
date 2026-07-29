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
    SearchHit,
    qualname_matches,
    search,
)

EVAL_MD = Path(__file__).resolve().parent.parent.parent / "docs" / "EVAL.md"
KS = (3, 5, 10)  # hit@3 shows reranker precision; hit@10 the recall gate
MAX_K = max(KS)


def _extract_yaml_block(text: str) -> str:
    """Return the first ```yaml fenced block's body from ``text``."""
    match = re.search(r"```yaml\n(.*?)\n```", text, re.DOTALL)
    if match is None:
        raise SystemExit("EVAL.md: no ```yaml question block found")
    return match.group(1)


def _parse_eval_md() -> tuple[str, str | None, list[dict]]:
    """Return (benchmark_url, pinned_sha, questions) parsed from EVAL.md."""
    text = EVAL_MD.read_text(encoding="utf-8")
    url_match = re.search(r"\((https://github\.com/[^)]+)\)", text)
    sha_match = re.search(r"Pinned commit:\*\*\s*`([0-9a-f]{7,40})`", text)
    questions = yaml.safe_load(_extract_yaml_block(text))
    if not isinstance(questions, list):
        raise SystemExit("EVAL.md: question block did not parse to a list")
    url = url_match.group(1) if url_match else "https://github.com/encode/httpx"
    sha = sha_match.group(1) if sha_match else None
    return url, sha, questions


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


async def run(modes: list[Mode], repo_ref: str, conditions: list[bool]) -> int:
    url, sha, questions = _parse_eval_md()
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

            head_sha = await conn.fetchval(
                "SELECT head_sha FROM repos WHERE id = $1", snapshot_id
            )
            n_chunks = await conn.fetchval(
                "SELECT count(*) FROM chunks WHERE snapshot_id = $1", snapshot_id
            )
            n_impl = await conn.fetchval(
                "SELECT count(*) FROM chunks WHERE snapshot_id = $1 AND NOT is_test",
                snapshot_id,
            )
            if sha and head_sha and not head_sha.startswith(sha[:12]):
                print(f"WARNING: ingested head {head_sha} != EVAL pinned {sha}")

            await _truth_file_guard(conn, snapshot_id, questions)

            for include_tests in conditions:
                print(f"\n>>> condition: {CONDITION_LABELS[include_tests]}")
                results.append(
                    await _measure(
                        conn, snapshot_id, questions, modes, include_tests=include_tests
                    )
                )
    finally:
        await close_pool(pool)

    total = len(questions)
    corpus = _corpus_line(n_chunks, n_impl)
    print(_format_report(modes, results, url, head_sha, corpus, total))
    _append_results(modes, results, url, head_sha, corpus, total)
    print(f"\nappended results block to {EVAL_MD}")
    return 0


def _corpus_line(n_chunks: int, n_impl: int) -> str:
    """Chunk counts split by ``is_test`` — the corpus the numbers came from."""
    return f"{n_chunks} chunks ({n_impl} implementation, {n_chunks - n_impl} test)"


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
    with EVAL_MD.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(block) + "\n")


def main(argv: list[str] | None = None) -> int:
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
        help="repo url or id (default: the benchmark repo from EVAL.md)",
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
    return asyncio.run(run(modes, args.repo, conditions))


if __name__ == "__main__":
    raise SystemExit(main())
