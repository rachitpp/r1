---
name: run-eval
description: Measure retrieval or answer quality on the frozen benchmark instead of guessing. Use whenever a change touches retrieval/, the chunker, the reranker, or agent prompts, or when asked whether something improved or hurt hit@k, recall, citation quality, or "is this better".
---

# Running the benchmark

Never assess retrieval by reading a handful of results. Run the script and report
numbers. Never hand-tune against individual questions — fix retrieval, not the
benchmark (EVAL.md rules, CLAUDE.md working agreement).

## Which script

- **Retrieval only** (hit@3 / hit@5 / hit@10) — `scripts/eval.py`. Fast, no model
  calls. This is the default choice.
- **Answer level** (answer-hit, citations) — `scripts/answer_eval.py`. Slow, spends
  API budget. Use `--limit` or `--questions` while iterating.

Both run from `backend/`:

    cd backend && uv run python scripts/eval.py --mode all

## scripts/eval.py

    uv run python scripts/eval.py --mode all
    uv run python scripts/eval.py --mode hybrid+rerank
    uv run python scripts/eval.py --mode all --both-conditions

- `--mode` — `all`, one mode, or a comma list. Modes: `vector`, `fts`, `hybrid`,
  `hybrid+rerank`.
- `--include-tests` / `--both-conditions` — test chunks are excluded from the
  candidate pool by default (SPEC §5.4). These flags select a measured *condition*,
  not a debug switch: the counterfactual is only real if both actually get run.
  Mutually exclusive.
- `--benchmark` — defaults to `docs/EVAL.md`. `docs/EVAL-FLASK.md` is the second
  repo's benchmark. Results append to whichever file is passed, so each keeps its
  own append-only history and neither can overwrite the other.
- `--repo` — url or id. Defaults to the repo named in the benchmark file.

## scripts/answer_eval.py

    uv run python scripts/answer_eval.py --mode both
    uv run python scripts/answer_eval.py --mode agent --limit 5 --no-append

`--mode stuffed|agent|both`, `--dev` (dev set instead of the frozen 20),
`--questions` (comma-separated ids), `--limit` (first N), `--pace` (seconds between
questions), `--tool-cap`, `--repo`, `--benchmark`, `--no-append`.

While iterating, use `--dev` or `--no-append`. Only append a real block when the run
is the one you intend to cite.

## Before trusting any number

Confirm the corpus is intact first — see [[verify-corpus]]. Expected 825 impl /
697 test. A wrong corpus produces plausible numbers, not an error.

## Interpreting the result

Compare against the recorded baselines, not against nothing:

- **hit@10 = 0.95 is a ceiling both chunkers already hit.** Fixed 1000-char windows
  tie AST there. A change that moves hit@10 has almost certainly not improved
  anything real. **hit@3** is where reranker precision actually shows.
- The case for AST rests on symbol-level and answer-level numbers, not retrieval
  hit-rate. Phase-3 finding (b): the agent leads at symbol level in 6/6 runs, sign
  stable but magnitude noisy (Mistral +5/+4/+2 vs 0.75, Vertex +1/+1/+2 vs 0.80).
  One run going the other way is noise, not a regression.
- Findings are **model-dependent** — q10 answered 3/3 on controlled temp-0 Mistral
  and 0/3 on Vertex. Always say which model produced a number.
- Finding (c): graph-tool use does **not** predict correctness. Do not report tool
  counts as evidence of quality.

## Recording

`eval.py` appends a dated results block to the benchmark file. **Never edit an
existing block.** Also report the numbers in your reply — don't make the user open
the file to find out what happened.
