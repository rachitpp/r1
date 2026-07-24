CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE repos (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  url           TEXT NOT NULL UNIQUE,
  name          TEXT NOT NULL,                -- "owner/repo"
  default_branch TEXT,
  head_sha      TEXT,
  status        TEXT NOT NULL DEFAULT 'queued',
    -- queued | cloning | parsing | embedding | ready | failed
  error         TEXT,
  files_total   INT NOT NULL DEFAULT 0,
  files_parsed  INT NOT NULL DEFAULT 0,
  chunks_total  INT NOT NULL DEFAULT 0,
  chunks_embedded INT NOT NULL DEFAULT 0,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
