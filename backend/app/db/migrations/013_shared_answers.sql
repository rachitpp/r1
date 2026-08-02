-- 013: shareable answer permalinks (SPEC §21, FEATURE-IDEAS 6.1).
--
-- An answer is worth sending to someone. Until now the only way was 6.2's
-- Markdown export — good for pasting into a PR, useless as a link. This stores
-- one answer under an unguessable id so a URL carries it.
--
-- **This is only sound because a snapshot is immutable (§14.3).** A permalink
-- promises that what the recipient sees is what the sender saw; that promise is
-- kept by the corpus being frozen, not by anything in this table. Citations
-- resolve against a pinned commit, so `_client.py:718-738` still means the same
-- lines a month later.
--
-- **Sharing is explicit.** Answers are not persisted as a side effect of
-- chatting — a transcript lives in sessionStorage (§9) and dies there unless
-- somebody presses Share. Storing every answer would grow unboundedly, and
-- silently retaining a user's questions is a different product decision than
-- letting them publish one.
--
-- **Reading needs no session, which is the whole point and the whole risk.**
-- The id is a random UUID and knowing it is the authorization — the "secret
-- link" model. What that discloses is the question, the answer, the cited paths
-- and line ranges, and the repo's name/URL/commit. In v1 every one of those is
-- derived from a PUBLIC GitHub repository, so the link reveals nothing that
-- `git clone` would not.
--
--   *** FEATURE-IDEAS 4.1 (private repositories) MUST revisit this table. ***
--
-- The moment a private corpus can exist, an anonymous read here leaks it. The
-- fix is a visibility column on the source and a check at both share and read
-- time; it is not written now because guessing at a schema for an unbuilt
-- feature ages worse than a loud comment. This is that comment.

CREATE TABLE shared_answers (
  -- Random, not sequential: the id IS the capability, so it must not be
  -- enumerable. gen_random_uuid() is v4 from pgcrypto, already used by §14.
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  snapshot_id UUID NOT NULL REFERENCES repo_snapshots(id) ON DELETE CASCADE,
  -- Who published it. Kept so the owner can unpublish (§21.4) and so a share
  -- is attributable; never exposed by the public read.
  created_by  UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  question    TEXT NOT NULL,
  answer      TEXT NOT NULL,
  -- Same shape and the same §7.5 validation as `snapshot_overviews.citations`.
  citations   JSONB NOT NULL DEFAULT '[]'::jsonb,
  -- Which model wrote it. A reader comparing two shared answers deserves to
  -- know whether they came from the same writer — same reasoning as §19's.
  model       TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The owner's "what have I published" list, and the unpublish path.
CREATE INDEX shared_answers_owner ON shared_answers (created_by, created_at DESC);
-- ON DELETE CASCADE from repo_snapshots needs this to avoid a seq scan per
-- snapshot delete; deleting a corpus must also retract its permalinks.
CREATE INDEX shared_answers_snapshot ON shared_answers (snapshot_id);
