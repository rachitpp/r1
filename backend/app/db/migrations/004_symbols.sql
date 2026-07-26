-- 004: symbol graph — definitions and import/call/extends edges (SPEC §3, §6).
--
-- Renumbered from 003 because 003 is `is_test` (DECISIONS 2026-07-26,
-- "Test shadowing"). SPEC §3's migration list carries the same order.
--
-- `symbols.is_test` mirrors the Phase 2 chunk flag: symbols and edges are
-- extracted from ALL files including tests, then filtered at the tool layer
-- (SPEC §6.3). Same flag-and-filter philosophy as §2.6/§5.4 — classify at
-- ingest, decide at query time, keep the counterfactual measurable.

CREATE TABLE symbols (
  id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  repo_id    UUID NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
  name       TEXT NOT NULL,        -- short name
  qualname   TEXT NOT NULL,        -- pkg.module.Class.method
  kind       TEXT NOT NULL,        -- function | method | class | module
  file_path  TEXT NOT NULL,
  start_line INT NOT NULL,
  end_line   INT NOT NULL,
  is_test    BOOLEAN NOT NULL DEFAULT FALSE,  -- from the file's §2.6 classification
  UNIQUE (repo_id, qualname, file_path, start_line)
);
CREATE INDEX symbols_name ON symbols (repo_id, name);
-- Tool lookups filter on is_test by default; index the common access path.
CREATE INDEX symbols_repo_is_test ON symbols (repo_id, is_test);

CREATE TABLE edges (
  id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  repo_id     UUID NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
  from_symbol BIGINT NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
  to_symbol   BIGINT NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
  kind        TEXT NOT NULL,       -- imports | calls | extends
  line        INT,                 -- site line in from_symbol's file
  UNIQUE (from_symbol, to_symbol, kind, line)
);
CREATE INDEX edges_from ON edges (from_symbol);
CREATE INDEX edges_to   ON edges (to_symbol);

ALTER TABLE chunks ADD COLUMN symbol_id BIGINT REFERENCES symbols(id);
-- backfilled during the symbol pass
CREATE INDEX chunks_symbol_id ON chunks (symbol_id);
