"""Retrieval evaluation against the frozen EVAL.md ground truth (SPEC §11).

Runs each benchmark question through one or more retrieval modes and reports
hit@5 / hit@10, then appends a dated results block to docs/EVAL.md. Never edits
an existing block, never tunes against individual questions — it only measures.

    uv run python scripts/eval.py --mode all
    uv run python scripts/eval.py --mode hybrid+rerank
"""

from __future__ import annotations

import argparse
import asyncio
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
from app.db.queries import resolve_repo_id  # noqa: E402
from app.retrieval.hybrid import (  # noqa: E402
    MODES,
    Mode,
    SearchHit,
    qualname_matches,
    search,
)

EVAL_MD = Path(__file__).resolve().parent.parent.parent / "docs" / "EVAL.md"
KS = (5, 10)
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


def _hit(hits: list[SearchHit], files: set[str], symbols: list[str], k: int) -> bool:
    """True if any of the top-``k`` hits satisfies file or symbol ground truth."""
    for h in hits[:k]:
        if h["file_path"] in files:
            return True
        if any(qualname_matches(h["symbol"], s) for s in symbols):
            return True
    return False


async def _truth_file_guard(
    conn: asyncpg.Connection, repo_id: UUID, questions: list[dict]
) -> None:
    """Warn loudly if any ground-truth file is missing from the files table.

    Catches path-format drift (Reconciliation 3) before it silently zeroes a
    question — a missing file can never be a hit.
    """
    rows = await conn.fetch("SELECT path FROM files WHERE repo_id = $1", repo_id)
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


async def run(modes: list[Mode], repo_ref: str) -> int:
    url, sha, questions = _parse_eval_md()
    ref = repo_ref or url
    settings = get_settings()
    pool = await create_pool(settings.DATABASE_URL)
    try:
        async with pool.acquire() as conn:
            repo_id = await resolve_repo_id(conn, ref)
            if repo_id is None:
                print(f"error: repo {ref!r} not ingested; run the CLI --db first")
                return 1

            head_sha = await conn.fetchval(
                "SELECT head_sha FROM repos WHERE id = $1", repo_id
            )
            n_chunks = await conn.fetchval(
                "SELECT count(*) FROM chunks WHERE repo_id = $1", repo_id
            )
            if sha and head_sha and not head_sha.startswith(sha[:12]):
                print(f"WARNING: ingested head {head_sha} != EVAL pinned {sha}")

            await _truth_file_guard(conn, repo_id, questions)

            # results[mode][k] = number of questions that hit; plus a per-q grid.
            hits: dict[Mode, dict[int, int]] = {m: {k: 0 for k in KS} for m in modes}
            grid: dict[str, dict[Mode, bool]] = {}
            for q in questions:
                truth = q.get("truth", {})
                files = set(truth.get("files", []))
                symbols = list(truth.get("symbols", []))
                grid[q["id"]] = {}
                for mode in modes:
                    found = await search(
                        conn, repo_id, q["question"], k=MAX_K, mode=mode
                    )
                    for k in KS:
                        if _hit(found, files, symbols, k):
                            hits[mode][k] += 1
                    grid[q["id"]][mode] = _hit(found, files, symbols, MAX_K)
    finally:
        await close_pool(pool)

    total = len(questions)
    report = _format_report(modes, hits, grid, url, head_sha, n_chunks, total)
    print(report)
    _append_results(modes, hits, grid, url, head_sha, n_chunks, total)
    print(f"\nappended results block to {EVAL_MD}")
    return 0


def _rate(n: int, total: int) -> str:
    return f"{n / total:.2f} ({n}/{total})"


def _summary_table(
    modes: list[Mode], hits: dict[Mode, dict[int, int]], total: int
) -> list[str]:
    lines = ["| Mode | hit@5 | hit@10 |", "|---|---|---|"]
    for mode in modes:
        lines.append(
            f"| {mode} | {_rate(hits[mode][5], total)} | "
            f"{_rate(hits[mode][10], total)} |"
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
    hits: dict[Mode, dict[int, int]],
    grid: dict[str, dict[Mode, bool]],
    url: str,
    head_sha: str | None,
    n_chunks: int,
    total: int,
) -> str:
    sha_short = (head_sha or "?")[:12]
    lines = [
        "=" * 60,
        f"eval: {url} @ {sha_short}  ({n_chunks} chunks, {total} questions)",
        "=" * 60,
        "",
        "hit@k summary:",
        *_summary_table(modes, hits, total),
        "",
        "per-question hit@10:",
        *_grid_table(modes, grid),
    ]
    return "\n".join(lines)


def _append_results(
    modes: list[Mode],
    hits: dict[Mode, dict[int, int]],
    grid: dict[str, dict[Mode, bool]],
    url: str,
    head_sha: str | None,
    n_chunks: int,
    total: int,
) -> None:
    today = dt.date.today().isoformat()
    sha_short = (head_sha or "?")[:12]
    block = [
        "",
        f"### Results — {today}",
        "",
        f"**Repo:** {url} @ `{sha_short}` — {n_chunks} chunks, "
        f"{total} questions. Modes: {', '.join(modes)}.",
        "",
        *_summary_table(modes, hits, total),
        "",
        "Per-question hit@10:",
        "",
        *_grid_table(modes, grid),
        "",
    ]
    with EVAL_MD.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(block) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scripts/eval.py")
    parser.add_argument(
        "--mode",
        default="all",
        choices=["all", *MODES],
        help="retrieval mode to evaluate (default: all)",
    )
    parser.add_argument(
        "--repo",
        default="",
        help="repo url or id (default: the benchmark repo from EVAL.md)",
    )
    args = parser.parse_args(argv)
    modes: list[Mode] = list(MODES) if args.mode == "all" else [args.mode]
    return asyncio.run(run(modes, args.repo))


if __name__ == "__main__":
    raise SystemExit(main())
