-- 008: let the retained legacy `repo_id` columns be NULL.
--
-- 007 kept `repo_id` on every content table so the snapshot split stays
-- revertible for one release (SPEC §14.8). What it did not do is drop the
-- NOT NULL those columns carried from 002/004 — and the new code writes only
-- `snapshot_id`. The result: every existing row reads back perfectly, so
-- retrieval and the whole eval suite pass, while **every new ingest fails** on
-- `null value in column "repo_id" violates not-null constraint`.
--
-- Caught by the §14 interleaving integration test, which is the only check that
-- performs a real ingest rather than reading the corpus 007 migrated. Worth
-- stating plainly: a schema change that is invisible to every read and fatal to
-- every write is exactly what a read-only verification misses.
--
-- Keeping the columns is still right — they are the rollback path. They simply
-- have to be optional to be kept. They are dropped in a later release once the
-- revert window closes; until then new rows carry NULL there and the old rows
-- keep their values, which is all a revert needs.

ALTER TABLE files   ALTER COLUMN repo_id DROP NOT NULL;
ALTER TABLE chunks  ALTER COLUMN repo_id DROP NOT NULL;
ALTER TABLE symbols ALTER COLUMN repo_id DROP NOT NULL;
ALTER TABLE edges   ALTER COLUMN repo_id DROP NOT NULL;

-- `user_repos` needs its primary key moved first: 006 declared it as
-- (user_id, repo_id), and a primary-key column cannot be nullable, so the
-- DROP NOT NULL above would fail on this table alone with
-- `column "repo_id" is in a primary key`.
--
-- Nothing is lost by moving it. 007 already created a UNIQUE index on
-- (user_id, snapshot_id), which is the same guarantee expressed against the
-- column the code now uses — so the pair stays unique throughout, with no
-- window in which a library could gain a duplicate row.
ALTER TABLE user_repos DROP CONSTRAINT user_repos_pkey;
ALTER TABLE user_repos ALTER COLUMN repo_id DROP NOT NULL;
ALTER TABLE user_repos ADD PRIMARY KEY USING INDEX user_repos_user_snapshot;
