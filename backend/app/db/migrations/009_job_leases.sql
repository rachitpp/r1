-- 009: job leases and in-flight dedup (SPEC §15, v2 phase V3).
--
-- Read §15.1 first: the premise V2.md gave for this work was wrong. The startup
-- sweep does NOT destroy a second worker's live job — `sweep_zombie_repos` is
-- already time-based (`updated_at < now() - ZOMBIE_AFTER_S`) rather than
-- startup-scoped, and `job_timeout` (900s) sits below `ZOMBIE_AFTER_S` (1200s)
-- so ARQ cancels a wedged job before the sweep can reach it. A second worker is
-- safe on the code as it stands.
--
-- What is actually missing is a *heartbeat*. Progress writes are incidental:
-- `linking` sets its status once and then runs Jedi resolution silently to
-- completion, so only the 900s job timeout bounds how long a healthy job can
-- look stale. That is the hole these columns close, plus two things the old
-- model could not express at all — which worker holds a row, and a hard
-- guarantee that two workers never ingest the same repo at once.

ALTER TABLE repo_snapshots
  ADD COLUMN claimed_by   TEXT,
  ADD COLUMN claimed_at   TIMESTAMPTZ,
  ADD COLUMN heartbeat_at TIMESTAMPTZ;

-- The sweep's access path: find expired leases without scanning the table.
CREATE INDEX repo_snapshots_lease ON repo_snapshots (status, heartbeat_at);

-- One in-flight snapshot per (source, strategy) — §15.3.
--
-- A PARTIAL unique index, so finished snapshots are unconstrained: a source
-- accumulates any number of `ready` snapshots over time (that is the whole
-- point of §14), but only one may be in flight.
--
-- Keyed on `strategy`, NOT on `commit_sha` as V2.md specified. `commit_sha` is
-- NULL until the clone reports it and Postgres treats NULLs as distinct in a
-- unique index, so a commit-based constraint would happily admit a hundred
-- queued duplicates — permitting exactly the work it was meant to prevent.
--
-- Created CONCURRENTLY is impossible inside migrate.py's transaction, and the
-- table is small, so a brief lock is acceptable here. Note this can fail on a
-- database that already holds duplicate in-flight rows; the SELECT below is the
-- pre-flight check an operator should run first:
--
--   SELECT source_id, strategy, count(*) FROM repo_snapshots
--    WHERE status IN ('queued','cloning','parsing','linking','embedding')
--    GROUP BY 1,2 HAVING count(*) > 1;
CREATE UNIQUE INDEX repo_snapshots_one_in_flight
  ON repo_snapshots (source_id, strategy)
  WHERE status IN ('queued', 'cloning', 'parsing', 'linking', 'embedding');
