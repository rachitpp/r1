-- 003: flag test chunks so retrieval can target implementation by default.
--
-- Test files are written in user vocabulary ("chunk", "stream", "auth") while
-- implementation code is terse, so test chunks systematically outrank the
-- implementation for natural-language questions — in the FTS leg and in the
-- cross-encoder alike (DECISIONS 2026-07-26, "test shadowing"). Tests stay in
-- the corpus and in the `files` table (read_file / list_directory still see
-- them); retrieval filters them out by default via `hybrid_search(
-- include_tests=False)`. Classification is a corpus-wide path rule applied at
-- ingest (SPEC §2.6) — no per-file judgment.
--
-- The Phase 3 symbols/edges migration becomes 004 (SPEC §3).

ALTER TABLE chunks ADD COLUMN is_test BOOLEAN NOT NULL DEFAULT FALSE;
