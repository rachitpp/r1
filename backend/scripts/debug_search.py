"""Retrieval debugger: dump every signal for one query side by side (SPEC §5).

For a repo + query, prints the per-signal candidate lists — vector, FTS, RRF
fusion, injected identifiers, and the final reranked order — each row labeled
``file_path :: symbol`` with its score. Build/keep this before trusting eval
numbers: when a question misses, this shows which signal failed.

    uv run python scripts/debug_search.py --repo <url|id> --query "..."
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from uuid import UUID

import asyncpg

# scripts/ is a sibling of app/, not a package — put backend/ on the path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.db.pool import close_pool, create_pool  # noqa: E402
from app.db.queries import resolve_repo_id  # noqa: E402
from app.retrieval.hybrid import (  # noqa: E402
    SearchHit,
    _fetch_rows,
    _inject_symbol_ids,
    extract_identifiers,
    search,
)

TOP_N = 10


def _label(hit: SearchHit) -> str:
    return f"{hit['file_path']} :: {hit['symbol']}"


def _print_hits(title: str, hits: list[SearchHit], score_label: str) -> None:
    print(f"\n{title}")
    print("-" * len(title))
    if not hits:
        print("  (no hits)")
        return
    for rank, hit in enumerate(hits, start=1):
        print(f"  {rank:>2}. {hit['score']:+.4f} {score_label:<6} {_label(hit)}")


async def _print_injection(
    conn: asyncpg.Connection, repo_id: UUID, query: str
) -> None:
    idents = extract_identifiers(query)
    print("\nSYMBOL INJECTION (§5.2)")
    print("----------------------")
    print(f"  identifiers extracted: {idents or '(none)'}")
    ids = await _inject_symbol_ids(conn, repo_id, query)
    rows = await _fetch_rows(conn, ids)
    if not ids:
        print("  matched chunks:        (none)")
        return
    print("  matched chunks:")
    for cid in ids:
        r = rows[cid]
        print(f"    - {r['file_path']} :: {r['symbol']} ({r['kind']})")


async def run(repo_ref: str, query: str) -> int:
    settings = get_settings()
    pool = await create_pool(settings.DATABASE_URL)
    try:
        async with pool.acquire() as conn:
            repo_id = await resolve_repo_id(conn, repo_ref)
            if repo_id is None:
                print(f"error: repo {repo_ref!r} not ingested")
                return 1

            print("=" * 70)
            print(f"query: {query!r}")
            print("=" * 70)

            _print_hits(
                "VECTOR (cosine similarity)",
                await search(conn, repo_id, query, k=TOP_N, mode="vector"),
                "sim",
            )
            _print_hits(
                "FTS (ts_rank)",
                await search(conn, repo_id, query, k=TOP_N, mode="fts"),
                "rank",
            )
            _print_hits(
                "HYBRID (RRF fusion)",
                await search(conn, repo_id, query, k=TOP_N, mode="hybrid"),
                "rrf",
            )
            await _print_injection(conn, repo_id, query)
            _print_hits(
                "HYBRID + RERANK (cross-encoder) — final",
                await search(conn, repo_id, query, k=TOP_N, mode="hybrid+rerank"),
                "ce",
            )
    finally:
        await close_pool(pool)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scripts/debug_search.py")
    parser.add_argument("--repo", required=True, help="repo url or id")
    parser.add_argument("--query", required=True, help="the search query")
    args = parser.parse_args(argv)
    return asyncio.run(run(args.repo, args.query))


if __name__ == "__main__":
    raise SystemExit(main())
