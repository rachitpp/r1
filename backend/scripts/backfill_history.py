"""Fill in §20 commit history for snapshots ingested before it existed.

History is written at ingest and nothing backfills it, so every snapshot that
predates §20 answers `GET /history` with ``indexed: false``. A full re-ingest
would fix that and cost an embedding run per repo — for data that has nothing
to do with embeddings.

**This script writes to `commits` and `commit_files` and to nothing else.** It
does not touch `chunks`, `symbols`, `edges` or any vector, so retrieval numbers
cannot move as a result of running it. That is the whole reason it exists
rather than `re-ingest everything`.

**It walks the snapshot's pinned commit, not HEAD.** A snapshot is frozen at
`commit_sha` (§14.3) and the repo has moved on since; filing today's history
under a months-old snapshot would be worse than leaving it empty. A snapshot
whose commit is not reachable in the deepened clone is **skipped and reported**,
never approximated.

Usage::

    uv run python scripts/backfill_history.py --dry-run   # what would run
    uv run python scripts/backfill_history.py             # all missing
    uv run python scripts/backfill_history.py --url https://github.com/pallets/flask
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.ingest.clone import cloned_repo  # noqa: E402
from app.ingest.pipeline import store_history  # noqa: E402

# Ready snapshots with no commit rows. `naive` corpora are included: §2.7 makes
# them a chunking baseline, not a different repo, and their history is as real
# as the AST corpus's at the same commit.
PENDING = """
SELECT sn.id, sn.commit_sha, sn.strategy, s.url, s.name
  FROM repo_snapshots sn
  JOIN repo_sources   s ON s.id = sn.source_id
 WHERE sn.status = 'ready'
   AND NOT EXISTS (SELECT 1 FROM commits c WHERE c.snapshot_id = sn.id)
   AND ($1::text IS NULL OR s.url = $1)
 ORDER BY s.url, sn.strategy
"""


def _has_commit(repo_dir: Path, sha: str) -> bool:
    """Whether ``sha`` is present in the (shallow) clone."""
    from git import Repo

    try:
        Repo(repo_dir).commit(sha)
        return True
    except Exception:
        return False


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", help="only this source URL")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="list what would be backfilled and exit without cloning",
    )
    args = ap.parse_args()

    conn = await asyncpg.connect(get_settings().DATABASE_URL)
    try:
        rows = await conn.fetch(PENDING, args.url)
        if not rows:
            print("nothing to backfill — every ready snapshot has history")
            return 0

        print(f"{len(rows)} snapshot(s) without history:")
        for r in rows:
            sha = (r["commit_sha"] or "-")[:8]
            print(f"  {r['name']:<32} {r['strategy']:<6} @{sha}")
        if args.dry_run:
            print("\n--dry-run: nothing cloned, nothing written")
            return 0

        print()
        ok = skipped = 0
        for r in rows:
            label = f"{r['name']} ({r['strategy']})"
            if not r["commit_sha"]:
                print(f"SKIP {label}: snapshot has no commit_sha to walk from")
                skipped += 1
                continue
            try:
                with cloned_repo(str(r["url"])) as info:
                    if not _has_commit(info.path, str(r["commit_sha"])):
                        print(
                            f"SKIP {label}: commit {str(r['commit_sha'])[:8]} is not "
                            "in the deepened clone — the repo has moved past it"
                        )
                        skipped += 1
                        continue
                    n_commits, n_touches = await store_history(
                        conn, r["id"], info.path, rev=str(r["commit_sha"])
                    )
                print(f"OK   {label}: {n_commits} commits, {n_touches} touches")
                ok += 1
            except Exception as exc:  # noqa: BLE001 - one repo must not stop the rest
                print(f"FAIL {label}: {type(exc).__name__}: {exc}")
                skipped += 1

        print(f"\nbackfilled {ok}, skipped {skipped}")
        return 0 if ok or not rows else 1
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
