# Phase 1 prompt — Parse & chunk (CLI only)

> **How to use:** start a fresh Claude Code session at the repo root and
> paste everything below the line. Phase 0 must be `done` in ROADMAP.md.

---

You are starting **Phase 1 — Parse & chunk** of this project.

## Step 0 — Orient

Read, in this order:
1. `CLAUDE.md`
2. `docs/ROADMAP.md` — the Phase 1 section
3. `docs/SPEC.md` — §2 (all of it), §11.1, §12
4. `docs/DECISIONS.md`

Confirm Phase 0 is marked done. Give me a plan of ≤10 lines, then proceed.

## Step 0.5 — Environment gate (do this before building anything)

This host has a WDAC policy that blocks unsigned native binaries (it
blocked ruff and mypy's compiled extension in Phase 0). tree-sitter is a
compiled native extension, so verify it actually loads:

```bash
cd backend
uv add tree-sitter tree-sitter-python gitpython
uv run python -c "import tree_sitter, tree_sitter_python; \
from tree_sitter import Parser, Language; \
p = Parser(Language(tree_sitter_python.language())); \
t = p.parse(b'def f(x: int) -> int:\n    return x'); \
print('tree-sitter OK:', t.root_node.type)"
git --version
```

- If the import fails with a DLL-load / policy error: **STOP.** Report
  the exact error and do not attempt workarounds, vendored builds, or
  alternative parsers. The human will move backend work to WSL2 or
  another machine.
- If `git` is missing: stop and say so (GitPython shells out to it).
- If both succeed, print the versions and continue.

## Session rules

- Build **only Phase 1**: no database, no embeddings, no HTTP, no agent
  code. The CLI is the only interface.
- Pre-authorized new deps: `gitpython`, `tree-sitter`,
  `tree-sitter-python`. Nothing else — in particular do NOT add
  `sentence-transformers`, `transformers`, `tokenizers`, `jedi`,
  `typer`, or `click`. The CLI uses stdlib `argparse`.
- ruff cannot execute on this host: write ruff-clean code, attempt
  `uv run ruff check .` once at the end, and if blocked note it as
  deferred (as in Phase 0). mypy runs via the pure-Python build you
  used in Phase 0.
- If this prompt conflicts with SPEC or ROADMAP, stop and flag it.
- Small logical commits.

## One SPEC reconciliation (do this first, log it)

SPEC §2.4's example header shows `# Symbol: AuthMiddleware.verify_token`
(class-relative), but §3 defines `qualname` as the full dotted path
(`pkg.module.Class.method`). Resolve in favor of the **full dotted
qualname everywhere** — chunk `symbol` fields and header `Symbol:` lines
both use it. Edit the §2.4 example to match and append a short
DECISIONS.md entry noting the reconciliation.

Module-path rule (this defines the qualname prefix):
`a/b/c.py` → `a.b.c`; `a/b/__init__.py` → `a.b`. A module chunk's
Symbol is the module path itself; a method's is
`<module>.<Class>.<method>`.

## Deliverables

### 1. `app/ingest/clone.py`
- `clone_repo(url: str) -> CloneInfo` — shallow clone
  (`--depth 1 --single-branch`) into a temp dir under the OS temp root;
  returns path, `head_sha`, `default_branch`.
- Context-manager (or try/finally) cleanup that always removes the
  work dir — including on failure. **Windows gotcha:** git object files
  are read-only; `shutil.rmtree` needs an `onexc`/`onerror` handler
  that clears the read-only bit before retrying the delete.

### 2. `app/ingest/filters.py`
`select_files(repo_dir) -> list[SourceFile]` implementing SPEC §2.2
exactly, in order: candidate set from `git ls-files`; keep `*.py` only;
drop any path containing an `IGNORE_DIRS` segment; drop files over
`MAX_FILE_BYTES`; drop files with a null byte in the first 8 KB; skip
(with a logged warning) files that fail UTF-8 decode; raise a typed
error if survivors exceed `MAX_FILES`. `SourceFile` carries rel path
(posix-style, forward slashes), text, and n_lines. Return skip counts
by reason for the CLI stats.

### 3. `app/ingest/tokens.py`
- `TokenCounter` protocol with `token_len(text: str) -> int`.
- `HeuristicTokenCounter`: `len(text) // 4`. This stands in for the
  real embedding tokenizer, which arrives in Phase 2 with
  sentence-transformers (native deps we are not installing today).
  Phase 2 swaps the implementation and re-checks oversize splits.
  Append a DECISIONS.md entry for this substitution.

### 4. `app/ingest/parser.py`
tree-sitter extraction per SPEC §2.3:
- **Module chunk** (0 or 1 per file): module docstring + import block +
  top-level assignments outside any def/class. Skip if trivially empty.
  Line range: 1 → end line of the last included statement.
- **Class skeleton chunk** per class: class line, docstring,
  class-level attributes, method *signatures* (bodies elided). Line
  range = the real class node span.
- **Function/method chunk** per def: decorators + def line + docstring
  + body. Nested defs stay inside their parent — no separate chunks
  below depth 1. Async and decorated defs must be captured.
- `start_line`/`end_line` are **1-based** (tree-sitter rows are
  0-based — add 1). A decorated function's start_line is its first
  decorator's line.
- Files where `root_node.has_error` is true: log a warning with the
  path and skip the file entirely. Never crash on bad syntax.
- Extract per file: the import-statement texts (for headers) and per
  chunk: qualname, kind, signature line.

### 5. `app/ingest/chunker.py`
- Assemble header + `\n---\n` + code exactly per SPEC §2.4 (with the
  full-qualname Symbol line from the reconciliation above). Imports
  line = file-level import statements joined with `; `.
- Oversize handling per §2.5: if `token_len(text) > CHUNK_TOKEN_MAX`,
  split the body on top-level statement boundaries into parts; each
  part repeats the full header plus `# Part: i/n`; set `part`/`n_parts`.
  Never split mid-statement or on character counts.
- Output dataclass mirrors the chunk fields from SPEC §3 minus
  DB/embedding fields: file_path, symbol, kind, part, n_parts,
  start_line, end_line, header, code.

### 6. `app/ingest/cli.py` (`python -m app.ingest.cli`)
- `<github_url>` positional; flags: `--dump PATH` (write all chunks as
  JSONL), `--sample N` (print N random full chunks, seeded RNG for
  reproducibility).
- Prints a stats block: repo name + head_sha; files kept / skipped by
  reason; chunk counts by kind; number of oversize splits; elapsed
  seconds.

### 7. Tests (`tests/ingest/`)
Fixture-driven (small inline Python sources are fine). Cover, at
minimum, the nine ROADMAP cases: top-level function, method, nested
function (stays inside parent), decorated function, async function,
class with docstring (skeleton correctness), oversized function
(statement-boundary split with Part headers), syntax-error file
(skipped + warned, no crash), empty file (no chunks, no crash). Plus:
filters (size cap, binary sniff, ignore-dir segment), module-path
derivation (`__init__.py` case), and 1-based line numbering for a
decorated def.

### 8. Benchmark run + `docs/EVAL.md`
1. Run the CLI against `https://github.com/encode/httpx`. Record the
   cloned `head_sha`. If httpx fails for a structural reason, fall back
   to `pallets/flask` and say why.
2. Write the 30-chunk sample to `docs/samples/phase1-sample.txt` (use
   `--sample 30`) for the human spot-check.
3. Draft the 20 EVAL questions per SPEC §11.1 by reading the cloned
   repo: roughly 7 exact/locate questions ("where is X defined"),
   8 conceptual ("how does X work"), 5 flow ("what happens when...").
   Verify every ground-truth file path exists at the pinned SHA and
   every listed symbol actually appears in that file.
4. **PAUSE.** Show me all 20 questions with their ground truth and wait
   for approval or edits before writing the final EVAL.md (pinned repo
   + SHA at the top, frozen-once-Phase-2-begins rule intact).

## Verification — run and show output

```bash
cd backend
uv run pytest
uv run mypy app          # pure-Python build, as in Phase 0
uv run ruff check .      # expected blocked on this host; note if so
uv run python -m app.ingest.cli https://github.com/encode/httpx --sample 5
```

Include the full stats block and the 5 sample chunks in your output.

## Wrap up

1. ROADMAP.md: Phase 1 → done, tick the checkboxes (the 30-chunk
   spot-check box is ticked by the human after review — leave it
   unticked and say so).
2. DECISIONS.md entries: qualname reconciliation; heuristic token
   counter; benchmark repo + SHA choice.
3. Final commit. ≤10-line summary: what exists, the benchmark stats,
   anything flagged.
