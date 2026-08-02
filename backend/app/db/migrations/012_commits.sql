-- 012: commit history — the "when and why" dimension (SPEC §20, FEATURE-IDEAS 2.1).
--
-- Everything else in this database describes the code as it is at one commit.
-- Nothing described how it got there, which is the question a newcomer asks
-- second and often first: "why is this like this?"
--
-- KEYED ON snapshot_id, like `files`/`chunks`/`symbols`/`edges`, and not on
-- `source_id`. History up to commit C is as immutable as the tree at commit C,
-- so it belongs to the snapshot under exactly the §14.3 argument that lets the
-- overview be cached forever. The cost is duplication: two snapshots of one
-- repo store the log twice. That is accepted deliberately — the alternative
-- ties a source's history to whichever snapshot happened to fetch it deepest,
-- and then "the history of THIS corpus" stops being answerable. Duplication is
-- bounded by HISTORY_MAX_COMMITS (§12); a wrong answer is not bounded by
-- anything.
--
-- Nothing here is backfilled. A snapshot ingested before this migration has no
-- commit rows, which reads as "history was not indexed", not as "this repo has
-- no history" — §20.4 makes that distinction visible at the API rather than
-- letting an empty list imply a young repo.

CREATE TABLE commits (
  id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  snapshot_id  UUID NOT NULL REFERENCES repo_snapshots(id) ON DELETE CASCADE,
  sha          TEXT NOT NULL,
  author_name  TEXT NOT NULL,
  -- Nullable: `git log` yields an empty author email often enough on old or
  -- imported history that NOT NULL would abort an ingest over a cosmetic field.
  author_email TEXT,
  -- Author date, not commit date: "when was this written" survives a rebase,
  -- which is what the question actually means. Both exist in git; storing the
  -- one we answer with keeps the column honest.
  authored_at  TIMESTAMPTZ NOT NULL,
  -- Split at ingest so the list view never parses prose. `subject` is the
  -- first line, `body` the remainder, NULL when there is none.
  subject      TEXT NOT NULL,
  body         TEXT,
  -- Flag-and-filter (§2.6, §6.3): a merge commit "touches" every file of the
  -- branch it absorbs, which makes it noise in a per-file history and signal
  -- in a release timeline. Classify at ingest, decide at query time.
  is_merge     BOOLEAN NOT NULL DEFAULT FALSE,
  UNIQUE (snapshot_id, sha)
);

-- The two access paths, both from §20.2: a repo-wide reverse-chronological
-- timeline, and the same scoped to one path (via commit_files below).
CREATE INDEX commits_snapshot_date ON commits (snapshot_id, authored_at DESC);

CREATE TABLE commit_files (
  commit_id   BIGINT NOT NULL REFERENCES commits(id) ON DELETE CASCADE,
  -- Denormalised from `commits` on purpose. The hot query is "history of path
  -- P in snapshot S", and carrying snapshot_id here makes that one index seek
  -- instead of a join that filters after the fact — the same reason 007 put
  -- snapshot_id directly on chunks and symbols rather than reaching through.
  snapshot_id UUID NOT NULL REFERENCES repo_snapshots(id) ON DELETE CASCADE,
  file_path   TEXT NOT NULL,
  insertions  INT NOT NULL DEFAULT 0,
  deletions   INT NOT NULL DEFAULT 0,
  PRIMARY KEY (commit_id, file_path)
);

CREATE INDEX commit_files_lookup ON commit_files (snapshot_id, file_path);
