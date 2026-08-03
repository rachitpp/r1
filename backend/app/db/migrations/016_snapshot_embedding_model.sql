-- 016: record which model produced a snapshot's vectors (SPEC §29, FEATURE-IDEAS 5.3).
--
-- Incremental re-indexing copies chunk rows — embeddings included — from an
-- earlier snapshot of the same repo when a file has not changed. That is only
-- sound if both snapshots' vectors live in the SAME vector space, and until now
-- nothing recorded which space that was: `EMBEDDING_MODEL` is read from the
-- environment at ingest and left nowhere on the row.
--
-- Without this column the reuse path would silently mix `bge-small` vectors
-- with whatever the operator switched to, and the failure would not look like a
-- failure — cosine distance over two different embedding spaces returns
-- perfectly plausible numbers. Retrieval would simply get quietly worse, on a
-- corpus that reports itself `ready`. That is the worst shape a bug can have
-- here, and it is why the column comes before the feature.
--
-- NULLABLE, and NULL means "unknown". Every snapshot ingested before today has
-- no record of its model, so reuse must refuse them rather than assume they
-- match the current setting — the same "not indexed is not the same as empty"
-- distinction §20.4 draws for history and §26.3 for dependencies. Those
-- snapshots still serve every read; they are just not eligible as a reuse
-- source, which costs one full re-embed, once.

ALTER TABLE repo_snapshots ADD COLUMN embedding_model TEXT;

-- The reuse lookup is "the newest ready snapshot of this source, same strategy,
-- same model". Source and strategy are already covered by the §15.3 indexes;
-- this keeps the model check from being the part that scans.
CREATE INDEX repo_snapshots_reuse_idx
  ON repo_snapshots (source_id, strategy, embedding_model, created_at DESC)
  WHERE status = 'ready';
