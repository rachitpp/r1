-- 014: multi-turn conversations (SPEC §23, FEATURE-IDEAS 4.4).
--
-- Until now a transcript lived in `sessionStorage` and died there, and every
-- answer was self-contained: asking "and where is that called?" got a run that
-- had never heard of "that". Two separate gaps, and this table closes both —
-- the agent gets prior turns as context, and the history survives the tab.
--
-- **Turns are stored, tool calls are not.** A run's tool timeline is large, is
-- already streamed to the client as it happens (§9), and is worthless as
-- context for the *next* question: what a follow-up needs is what was asked and
-- what was concluded. Storing the timeline too would multiply the row size and
-- the prompt for no gain. §21's `shared_answers` made the same call.
--
-- **Bounded by construction, not by hope.** §23.2 feeds at most
-- `CONVERSATION_CONTEXT_TURNS` turns into a prompt, each truncated to
-- `CONVERSATION_ANSWER_CHARS`. Those are §12 constants rather than settings
-- because they bound what a *request costs*, which is part of the API's
-- contract on a tier measured in requests per day.
--
-- **Scoped to a snapshot, not a source.** A conversation is about a corpus. The
-- citations in its turns resolve against one immutable snapshot (§14.3), so
-- letting a conversation span two snapshots of the same repo would make its own
-- history cite lines that no longer mean what they meant.

CREATE TABLE conversations (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  snapshot_id UUID NOT NULL REFERENCES repo_snapshots(id) ON DELETE CASCADE,
  user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  -- The first question, trimmed. Named at creation rather than generated: a
  -- title is worth zero model calls, and "what a user actually typed first" is
  -- a better label than a summary of it would be.
  title       TEXT NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  -- Ordering for the resume list. Bumped on every stored turn, so "recent"
  -- means recently *used*, not recently created.
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The sidebar query: this user's conversations for this repo, newest activity
-- first. Both columns, because a user with one repo open should not scan rows
-- belonging to their others.
CREATE INDEX conversations_owner
  ON conversations (user_id, snapshot_id, updated_at DESC);

CREATE TABLE conversation_turns (
  conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  -- 1-based position. Part of the key rather than a timestamp ordering: two
  -- turns cannot share a position, which is a stronger guarantee than "their
  -- clocks differed" and makes the context window a simple ORDER BY.
  ordinal         INT NOT NULL,
  question        TEXT NOT NULL,
  answer          TEXT NOT NULL,
  -- Same shape and the same §7.5 validation as everywhere else citations are
  -- stored. Kept so a resumed conversation renders its chips without re-running
  -- anything.
  citations       JSONB NOT NULL DEFAULT '[]'::jsonb,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (conversation_id, ordinal)
);
