-- 006: users and per-user repo libraries (SPEC §13.2, v2 phase V1).
--
-- v1 shipped single-user by design. Every /repos route resolved a repo by id
-- and checked only that it existed, so any caller holding a UUID could list,
-- chat over, and read the files of any repo in the database. This is the
-- schema half of closing that; the enforcement half is §13.5.
--
-- Purely additive. No existing table is altered and no row is rewritten — the
-- 2026-07-29 span migration is a standing reminder that rewriting rows
-- reshuffles physical order, and there is no reason to do it here.
--
-- `github_id` is the identity, never `login`: GitHub accounts can be renamed,
-- and a renamed account that came back as a new row would silently orphan a
-- user's library. `login` is refreshed on every sign-in instead.

CREATE TABLE users (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  github_id    BIGINT NOT NULL UNIQUE,
  login        TEXT NOT NULL,
  name         TEXT,
  avatar_url   TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE user_repos (
  user_id  UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  repo_id  UUID NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
  added_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, repo_id)
);

-- `GET /repos` filters by user_id, which the composite PK's leading column
-- already serves. This one is for the reverse question — "who owns this repo"
-- — which the ownership check and any future GC both ask.
CREATE INDEX user_repos_repo ON user_repos (repo_id);

-- §13.7: every pre-auth repo is adopted by a bootstrap user. An unowned repo
-- is unreachable once §13.5 lands, so leaving these behind would present as
-- data loss. github_id 0 is a placeholder no real GitHub account can hold
-- (ids are positive); the first sign-in that matches BOOTSTRAP_GITHUB_ID
-- takes the row over, and its library with it.
INSERT INTO users (github_id, login, name)
VALUES (0, 'bootstrap', 'Pre-auth owner (SPEC §13.7)')
ON CONFLICT (github_id) DO NOTHING;

INSERT INTO user_repos (user_id, repo_id)
SELECT u.id, r.id
  FROM users u CROSS JOIN repos r
 WHERE u.github_id = 0
ON CONFLICT DO NOTHING;
