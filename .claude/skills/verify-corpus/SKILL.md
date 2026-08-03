---
name: verify-corpus
description: Check that the ingested benchmark corpus is intact (encode/httpx @ b5addb64, 825 impl / 697 test chunks) before trusting eval numbers, or when chunk counts, retrieval results, or the database look wrong or surprisingly large.
---

# Verifying the benchmark corpus

The database (Neon) holds the ingested benchmark corpus. Check it before citing any
eval number — a wrong corpus yields plausible numbers rather than an error.

## The query

Run against `DATABASE_URL` (DSN lives in `backend/.env`); `psql` is the established
route for direct inspection.

```sql
SELECT count(*) FILTER (WHERE NOT c.is_test) AS impl,
       count(*) FILTER (WHERE c.is_test)     AS test
  FROM chunks c
  JOIN repo_snapshots sn ON sn.id = c.snapshot_id
  JOIN repo_sources   s  ON s.id  = sn.source_id
 WHERE s.url = 'https://github.com/encode/httpx'
   AND sn.strategy = 'ast';
```

Expected: **825 | 697**.

## The scoping is load-bearing

Do not shorten this to a bare `FROM chunks`. That was the original wording and it
stopped being correct the moment a second repo was ingested.

The database now holds **seven sources** and **two httpx corpora** — the `ast` one
and the SPEC §2.7 `naive` baseline, both at the *same* commit. Unscoped, the query
returns `1555 | 1170`, which reads as a corrupted benchmark and is not.

**Both** filters are required. Either one alone still counts the wrong rows:

- Without `s.url` — counts all seven sources.
- Without `sn.strategy` — counts the `ast` and `naive` httpx corpora together.

(Corrected 2026-07-31. If a future reader is tempted to simplify this again, this
paragraph is why not.)

## Reading the result

- **825 | 697** — corpus is good. Proceed; see [[run-eval]].
- **1555 | 1170** — the query lost its scoping. Not corruption. Restore both filters.
- **Anything else** — genuinely unexpected. Check `repo_snapshots.status` and
  `commit_sha` for the httpx `ast` row before concluding anything.

## Re-ingesting

Re-ingest **only** if the schema or the chunker changed. It is not a routine fix, and
it invalidates comparison against every previously recorded results block.
