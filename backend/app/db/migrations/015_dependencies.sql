-- 015: third-party dependencies — declared, and actually used (FEATURE-IDEAS 2.5).
--
-- The symbol graph describes what this repo contains. Nothing described what it
-- STANDS ON, which is a large part of understanding an unfamiliar codebase and
-- the question `§6.1` deliberately refuses to answer: an import resolved into
-- site-packages is dropped by design, because the graph is about this
-- repository and not its dependency tree.
--
-- TWO TABLES, NOT ONE, because the two halves are collected by different means
-- and either can be empty for a real repo:
--
--   `dependencies`     what the manifests declare. Absent for a repo with no
--                      pyproject.toml and no requirements.txt — common, and not
--                      an error.
--   `dependency_uses`  every non-relative import site in the corpus. Present
--                      even when nothing is declared, because it comes from the
--                      code rather than from a manifest.
--
-- The gap between them is the product: declared-but-never-imported, and
-- imported-but-never-declared. Folding them into one table would force a join
-- key that does not exist — a distribution name and a module name are not the
-- same string (`PyYAML` ships `yaml`), so matching is a normalised best effort
-- made at read time and reported as such, never asserted in the schema.
--
-- KEYED ON snapshot_id, like `files`/`chunks`/`symbols`/`edges`/`commits`, and
-- for the same §14.3 reason: a snapshot is immutable, so what it imports cannot
-- change, so these rows never need invalidating.
--
-- Nothing here is backfilled. A snapshot ingested before this migration has no
-- rows, which the API reports as "dependencies were not indexed" rather than
-- letting an empty list read as "this project has no dependencies" — the same
-- distinction §20.4 draws for commit history.

CREATE TABLE dependencies (
  id          BIGSERIAL PRIMARY KEY,
  snapshot_id UUID NOT NULL REFERENCES repo_snapshots(id) ON DELETE CASCADE,
  -- PEP 503 normalised, so `Flask-SQLAlchemy` and `flask_sqlalchemy` are one
  -- name. The spelling as written survives in `requirement`.
  name        TEXT NOT NULL,
  requirement TEXT NOT NULL,
  -- Which manifest asked for it: `pyproject.toml`, `requirements-dev.txt`, …
  source      TEXT NOT NULL,
  -- The optional-dependency group, when it came from one. NULL is a main
  -- dependency, and the difference matters: a package needed only by `[dev]`
  -- is not something a user of this library installs.
  extra       TEXT,
  -- One row per (package, manifest, group). The same package declared in two
  -- files stays two rows on purpose — which file wants it is part of the answer.
  UNIQUE (snapshot_id, name, source, extra)
);

CREATE INDEX dependencies_snapshot_idx ON dependencies (snapshot_id);

CREATE TABLE dependency_uses (
  id          BIGSERIAL PRIMARY KEY,
  snapshot_id UUID NOT NULL REFERENCES repo_snapshots(id) ON DELETE CASCADE,
  -- Top-level package only: `werkzeug.security` is recorded as `werkzeug`,
  -- because that is the unit that gets installed and declared.
  module      TEXT NOT NULL,
  -- The dotted path as written, so "where is werkzeug used" can still say
  -- which part of it.
  dotted      TEXT NOT NULL,
  -- stdlib | first_party | third_party. stdlib and first-party rows are stored
  -- rather than filtered at ingest: "does this repo use `asyncio` anywhere" is
  -- the same question in a different bucket, and re-ingesting to answer it
  -- later would be the expensive way to find out.
  kind        TEXT NOT NULL,
  file_path   TEXT NOT NULL,
  start_line  INTEGER NOT NULL,
  is_test     BOOLEAN NOT NULL DEFAULT FALSE
);

-- The two shapes the API reads: "summarise this snapshot's dependencies" and
-- "show me every use of package X".
CREATE INDEX dependency_uses_snapshot_kind_idx
  ON dependency_uses (snapshot_id, kind);
CREATE INDEX dependency_uses_module_idx
  ON dependency_uses (snapshot_id, module);
