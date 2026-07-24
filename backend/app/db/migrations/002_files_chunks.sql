-- 002: files + chunks (SPEC §3). Symbols/edges arrive in 003 (Phase 3).
-- The `vector` extension is created in 001_init.sql.

CREATE TABLE files (
  id       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  repo_id  UUID NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
  path     TEXT NOT NULL,
  content  TEXT NOT NULL,
  n_lines  INT  NOT NULL,
  UNIQUE (repo_id, path)
);

CREATE TABLE chunks (
  id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  repo_id    UUID NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
  file_path  TEXT NOT NULL,
  symbol     TEXT,            -- qualified name; module path for module chunks
  kind       TEXT NOT NULL,   -- function | method | class | module
  part       INT NOT NULL DEFAULT 1,
  n_parts    INT NOT NULL DEFAULT 1,
  start_line INT NOT NULL,
  end_line   INT NOT NULL,
  header     TEXT NOT NULL,
  code       TEXT NOT NULL,
  embedding  vector(384) NOT NULL,   -- dim tied to EMBEDDING_MODEL; changing
                                     -- models = new migration + full re-embed
  tsv tsvector GENERATED ALWAYS AS
      (to_tsvector('english', header || ' ' || code)) STORED
);
CREATE INDEX chunks_hnsw ON chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX chunks_tsv  ON chunks USING gin (tsv);
CREATE INDEX chunks_repo_file ON chunks (repo_id, file_path);
