-- 010: the (source, commit, strategy) uniqueness applies to READY snapshots only.
--
-- 007 made it unconditional, and that bricks a commit after any transient
-- failure. Found by the V3 three-worker run (DECISIONS 2026-07-30), not by
-- reading the code:
--
--   1. snapshot A clones, records commit_sha, then its worker is killed
--   2. the lease sweep marks A `failed` — but A KEEPS its commit_sha
--   3. a retry, snapshot B, clones the same repo and gets the same sha
--   4. `set_repo_clone_info` on B violates the unique key against the corpse
--
--   UniqueViolationError: duplicate key value violates unique constraint
--   "repo_snapshots_source_id_commit_sha_strategy_key"
--   Key (source_id, commit_sha, strategy)=(…, 87072f6d…, ast) already exists.
--
-- So one worker death made that commit permanently un-ingestable. Every retry
-- failed the same way, and nothing in the error text pointed at the cause.
--
-- The constraint's purpose is "one stored corpus per repo, commit and
-- strategy" (SPEC §14.2). A failed snapshot is not a corpus — it is a partial
-- write nobody can read, because a non-`ready` snapshot is not servable. So the
-- index belongs on `status = 'ready'`, which is exactly the population
-- `find_ready_snapshot` searches for §14.4 dedup.
--
-- Safe against two attempts racing to `ready`: 009's one-in-flight index already
-- permits only a single in-flight snapshot per (source_id, strategy), and §14.4
-- short-circuits a second attempt at a commit that is already stored before it
-- ingests anything.

ALTER TABLE repo_snapshots
  DROP CONSTRAINT repo_snapshots_source_id_commit_sha_strategy_key;

CREATE UNIQUE INDEX repo_snapshots_one_ready
  ON repo_snapshots (source_id, commit_sha, strategy)
  WHERE status = 'ready';
