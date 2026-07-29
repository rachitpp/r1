-- 005: correct the line spans of split class and module chunks.
--
-- A class chunk is a skeleton (method bodies elided, SPEC §2.2) and a module
-- chunk gathers the docstring, imports, and top-level assignments while
-- stepping over every def and class between them. Neither text is a contiguous
-- slice of the file, so the §2.5 oversize splitter's per-part arithmetic —
-- `start_line + <offset into the chunk's own code>` — produced line numbers
-- that name positions in the *rendering*, not in the source.
--
-- Measured on the benchmark corpus (DECISIONS 2026-07-29): 68 of 85 split class
-- chunks and 6 of 19 split module chunks carried a span that does not contain
-- their own first line of code. `httpx._client.AsyncClient` numbered its 18
-- parts 1307-1351, then 1352, 1353, 1354 … one line per elided method, for a
-- class whose real span is 1307-2019. 12 of the 200 chunks returned across the
-- 20 frozen EVAL questions came from that population; a citation into one
-- highlights code the model never read (hard rule 5).
--
-- `chunker.py` now reports the whole node span for every part of a rendered
-- chunk. This backfills rows written before that fix, from the `symbols` table,
-- which already stores the true tree-sitter span for each definition. Verified
-- to join 85/85 class and 19/19 module split chunks on the benchmark corpus.
--
-- Touches `start_line` / `end_line` only. Chunk text, embeddings, `symbol`, and
-- `file_path` are untouched, so retrieval order cannot change — and neither
-- eval metric reads a line number (hit@k scores file/symbol, `eval.py:80-81`;
-- answer-hit scores the citation's file, SPEC §11.2). Re-running `eval.py` after
-- this must reproduce the previous numbers exactly.

UPDATE chunks c
   SET start_line = s.start_line,
       end_line   = s.end_line
  FROM symbols s
 WHERE s.repo_id  = c.repo_id
   AND s.qualname = c.symbol
   AND s.kind     = c.kind
   AND c.kind     IN ('class', 'module')
   AND c.n_parts  > 1
   AND (c.start_line, c.end_line) IS DISTINCT FROM (s.start_line, s.end_line);
