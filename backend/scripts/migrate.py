"""Apply numbered SQL migrations idempotently.

Scans ``app/db/migrations`` for ``NNN_*.sql`` files, applies any whose version
is not yet recorded in ``schema_migrations``, each inside its own transaction.
Running twice in a row is a no-op. Non-zero exit on failure.

Usage:
    uv run python scripts/migrate.py
"""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

import asyncpg

# scripts/ is a sibling of app/, not a package — put backend/ on the path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "app" / "db" / "migrations"
FILENAME_RE = re.compile(r"^(\d+)_.*\.sql$")


def discover_migrations() -> list[tuple[int, Path]]:
    """Return ``(version, path)`` pairs sorted by numeric version."""
    found: list[tuple[int, Path]] = []
    for path in MIGRATIONS_DIR.glob("*.sql"):
        match = FILENAME_RE.match(path.name)
        if match is None:
            continue
        found.append((int(match.group(1)), path))
    found.sort(key=lambda item: item[0])
    return found


async def ensure_migrations_table(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
          version    INT PRIMARY KEY,
          applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


async def applied_versions(conn: asyncpg.Connection) -> set[int]:
    rows = await conn.fetch("SELECT version FROM schema_migrations")
    return {row["version"] for row in rows}


async def run() -> int:
    settings = get_settings()
    conn = await asyncpg.connect(settings.DATABASE_URL)
    applied_count = 0
    skipped_count = 0
    try:
        await ensure_migrations_table(conn)
        already = await applied_versions(conn)
        for version, path in discover_migrations():
            if version in already:
                print(f"skip   {path.name} (version {version} already applied)")
                skipped_count += 1
                continue
            sql = path.read_text(encoding="utf-8")
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO schema_migrations (version) VALUES ($1)", version
                )
            print(f"apply  {path.name} (version {version})")
            applied_count += 1
    finally:
        await conn.close()

    print(f"\ndone: {applied_count} applied, {skipped_count} skipped")
    return 0


def main() -> None:
    try:
        code = asyncio.run(run())
    except Exception as exc:  # noqa: BLE001 — surface any failure with nonzero exit
        print(f"migration failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    raise SystemExit(code)


if __name__ == "__main__":
    main()
