-- 007: split corpus identity from the user's library (SPEC §14, v2 phase V2).
--
-- v1 made a repo a singleton keyed by URL, and `pipeline.py` cleared its
-- content at the *start* of every ingest. So one user re-ingesting a repo
-- deleted the corpus another user was mid-chat on — silently, and after V1
-- any signed-in user could trigger it. The fix is not a lock: it is to stop
-- mutating a corpus anyone might be reading. A ready snapshot is frozen, and a
-- new commit is a new snapshot (§14.3).
--
-- DATA-PRESERVING. This migration rewrites rows and RE-EMBEDS NOTHING; every
-- vector keeps its value, which is what makes the §14.9 check meaningful:
-- `scripts/eval.py` must reproduce the recorded baseline question for question,
-- because retrieval is a pure function of the corpus.
--
-- Pre-migration fingerprint, for the record — sha256 over `id:embedding::text`
-- of all 1522 httpx chunks, ordered by id:
--   17fb8fc8ad9f6213ccec7b507ec5fa7c734403b104457a9a0f58bdad6b4a7551

CREATE TABLE repo_sources (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  url        TEXT NOT NULL UNIQUE,   -- canonical; the `#naive` fragment is gone
  name       TEXT NOT NULL,          -- "owner/repo"
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE repo_snapshots (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id       UUID NOT NULL REFERENCES repo_sources(id) ON DELETE CASCADE,
  commit_sha      TEXT,
  strategy        TEXT NOT NULL DEFAULT 'ast',   -- ast | naive (SPEC §2.7)
  default_branch  TEXT,
  status          TEXT NOT NULL DEFAULT 'queued',
  error           TEXT,
  files_total     INT NOT NULL DEFAULT 0,
  files_parsed    INT NOT NULL DEFAULT 0,
  chunks_total    INT NOT NULL DEFAULT 0,
  chunks_embedded INT NOT NULL DEFAULT 0,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  -- `strategy` belongs in this key and the V2.md plan's two-column version was
  -- wrong: httpx's AST and naive corpora sit at the SAME commit b5addb64 and
  -- are kept apart today only by the `#naive` URL fragment this migration
  -- retires. Without `strategy` here the second one cannot be inserted.
  --
  -- `commit_sha` is NULL until the clone reports it, and Postgres treats NULLs
  -- as distinct, so several queued attempts for one source coexist. That is
  -- correct — they are separate attempts. In-flight dedup is V3's lease work.
  UNIQUE (source_id, commit_sha, strategy)
);

-- One source per distinct URL. The two httpx rows collapse onto one: the
-- fragment is stripped from the URL and the `@naive` suffix from the name.
-- DISTINCT ON keeps the non-naive row's spelling, which is the canonical one.
INSERT INTO repo_sources (url, name)
SELECT DISTINCT ON (split_part(url, '#', 1))
       split_part(url, '#', 1),
       CASE WHEN name LIKE '%@naive' THEN left(name, length(name) - 6) ELSE name END
  FROM repos
 ORDER BY split_part(url, '#', 1), (url LIKE '%#naive');

-- One snapshot per existing repo row, KEEPING ITS UUID (§14.8). That makes the
-- user_repos rewrite below a rename rather than a remap, and means every repo
-- id already handed to a browser still resolves after the migration.
INSERT INTO repo_snapshots (
  id, source_id, commit_sha, strategy, default_branch, status, error,
  files_total, files_parsed, chunks_total, chunks_embedded, created_at, updated_at
)
SELECT r.id,
       s.id,
       r.head_sha,
       CASE WHEN r.url LIKE '%#naive' THEN 'naive' ELSE 'ast' END,
       r.default_branch, r.status, r.error,
       r.files_total, r.files_parsed, r.chunks_total, r.chunks_embedded,
       r.created_at, r.updated_at
  FROM repos r
  JOIN repo_sources s ON s.url = split_part(r.url, '#', 1);

-- Content tables point at the snapshot. `repo_id` is KEPT for one release
-- (§14.8): dropping it in the same migration that adds `snapshot_id` would
-- turn a rollback into a restore-from-backup.
ALTER TABLE files   ADD COLUMN snapshot_id UUID REFERENCES repo_snapshots(id) ON DELETE CASCADE;
ALTER TABLE chunks  ADD COLUMN snapshot_id UUID REFERENCES repo_snapshots(id) ON DELETE CASCADE;
ALTER TABLE symbols ADD COLUMN snapshot_id UUID REFERENCES repo_snapshots(id) ON DELETE CASCADE;
ALTER TABLE edges   ADD COLUMN snapshot_id UUID REFERENCES repo_snapshots(id) ON DELETE CASCADE;

-- A plain copy, because snapshot ids ARE the old repo ids.
UPDATE files   SET snapshot_id = repo_id;
UPDATE chunks  SET snapshot_id = repo_id;
UPDATE symbols SET snapshot_id = repo_id;
UPDATE edges   SET snapshot_id = repo_id;

ALTER TABLE files   ALTER COLUMN snapshot_id SET NOT NULL;
ALTER TABLE chunks  ALTER COLUMN snapshot_id SET NOT NULL;
ALTER TABLE symbols ALTER COLUMN snapshot_id SET NOT NULL;
ALTER TABLE edges   ALTER COLUMN snapshot_id SET NOT NULL;

-- Mirror every access path that existed on repo_id, or the first query through
-- the new column is a sequential scan of the whole corpus.
CREATE INDEX chunks_snapshot_file  ON chunks  (snapshot_id, file_path);
CREATE INDEX symbols_snapshot_name ON symbols (snapshot_id, name);
CREATE INDEX symbols_snapshot_test ON symbols (snapshot_id, is_test);
CREATE UNIQUE INDEX files_snapshot_path ON files (snapshot_id, path);
CREATE UNIQUE INDEX symbols_snapshot_qualname
  ON symbols (snapshot_id, qualname, file_path, start_line);
CREATE INDEX edges_snapshot ON edges (snapshot_id);

-- The library points at snapshots (§14.2). Same rename-not-remap trick.
ALTER TABLE user_repos ADD COLUMN snapshot_id UUID REFERENCES repo_snapshots(id) ON DELETE CASCADE;
UPDATE user_repos SET snapshot_id = repo_id;
ALTER TABLE user_repos ALTER COLUMN snapshot_id SET NOT NULL;
CREATE UNIQUE INDEX user_repos_user_snapshot ON user_repos (user_id, snapshot_id);
CREATE INDEX user_repos_snapshot ON user_repos (snapshot_id);
