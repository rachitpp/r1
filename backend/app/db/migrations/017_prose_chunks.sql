-- 017: mark prose/config chunks so retrieval can exclude them (SPEC §30.4).
--
-- §30 puts documentation, manifests and CI config into the corpus. That is the
-- point of the feature, and it is also the risk: a README is not merely user
-- vocabulary, it is the *same prose register as the question itself*. Blended
-- into the default candidate pool it would outrank implementation harder than
-- test chunks ever did, and "how does auth work" would answer with a paragraph
-- about auth instead of the code.
--
-- So the flag exists before the chunks do. §5.4 already proved the shape of this
-- problem for tests (DECISIONS 2026-07-26); this is the same exclusion, one
-- class of chunk later.
--
-- A FLAG, NOT A `kind` PREDICATE, deliberately — the same reasoning as `is_test`.
-- `kind` says what a chunk *is*; `is_prose` says how retrieval should *treat*
-- it. Conflating them makes the exclusion `kind NOT IN ('document','config')`,
-- which silently acquires a new member every time a `kind` is added and fails
-- open — the worst direction for a filter whose job is keeping prose out.
--
-- DEFAULT false backfills every existing chunk correctly: nothing ingested
-- before §30 is prose, because nothing but `*.py` was ever selected.
--
-- IDEMPOTENT, for a specific reason. The shared Neon database already carried
-- this column and index when this file was written: a version-17 row was
-- recorded on 2026-08-03 by a migration that was never committed and is in no
-- branch. `migrate.py` keys on the version number, so it skips this file there
-- forever, and only a fresh database will ever execute it. The guards below,
-- and the index definition matching the one that database actually has, are
-- what keep the two from diverging. See DECISIONS 2026-08-04.

ALTER TABLE chunks ADD COLUMN IF NOT EXISTS is_prose BOOLEAN NOT NULL DEFAULT false;

-- Every candidate-producing query in §5.1 filters on this *inside* each fusion
-- CTE, before the per-leg LIMIT — so the column sits in the same access pattern
-- `snapshot_id` already leads.
CREATE INDEX IF NOT EXISTS chunks_snapshot_prose_idx ON chunks (snapshot_id, is_prose);
