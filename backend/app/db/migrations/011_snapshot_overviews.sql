-- 011: the generated "start here" overview for a snapshot (SPEC §19).
--
-- One row per snapshot, and the PRIMARY KEY is what makes "generate exactly
-- once" a database guarantee rather than a lock somebody has to remember to
-- take. Two browsers opening the same repo page race to INSERT; one wins, the
-- other's `ON CONFLICT DO NOTHING` returns nothing and it polls instead. Same
-- reasoning as §15.3's in-flight index: the database is already the source of
-- truth and cannot drift from itself.
--
-- **No invalidation, ever.** A snapshot is frozen once ready (§14.3), so the
-- overview of a snapshot is a pure function of a corpus that cannot change.
-- That is the same property that makes the §17.5 answer cache correct, and it
-- is why this table has no `updated_at` and no staleness check: there is no
-- state in which a stored overview describes something other than its snapshot.
-- A new commit is a new snapshot and therefore a new row.
--
-- Cost is the reason this is stored at all. Generation is one model call, and
-- the tuning provider's free tier is 20 requests/day/model (see
-- `app/agent/model.py`) — roughly what a handful of repo pages would burn in an
-- afternoon if this were computed per view.

CREATE TABLE snapshot_overviews (
  snapshot_id UUID PRIMARY KEY REFERENCES repo_snapshots(id) ON DELETE CASCADE,
  -- generating | ready | failed. No 'queued': the row is created by the same
  -- statement that decides to enqueue, so it is claimed from the instant it
  -- exists.
  status      TEXT NOT NULL DEFAULT 'generating',
  body        TEXT,
  -- Validated (§7.5) before storage, so nothing downstream re-checks them.
  citations   JSONB NOT NULL DEFAULT '[]'::jsonb,
  -- Which model wrote it. The overview is model-dependent prose and a reader
  -- comparing two repos deserves to know they came from the same writer.
  model       TEXT,
  error       TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The retry path. A `failed` row must not block a second attempt forever —
-- that is exactly the bug `010` had to fix for snapshots, where an
-- unconditional unique constraint made one worker death permanent. Deleting a
-- failed row is the whole retry, and this index keeps finding them cheap.
CREATE INDEX snapshot_overviews_failed
  ON snapshot_overviews (snapshot_id)
  WHERE status = 'failed';
