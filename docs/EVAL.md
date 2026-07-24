# EVAL.md — frozen benchmark

**STATUS: NOT YET WRITTEN.** To be authored blind in Phase 1 — after
chunking works, before ANY retrieval code exists (ROADMAP Phase 1).

Benchmark repo: **TBD** (candidates: encode/httpx, pallets/flask).
Pin `owner/name` + commit SHA here when chosen.

Question format (SPEC §11.1):

    - id: q01
      question: "Where are request timeouts enforced?"
      truth:
        files: ["httpx/_config.py"]
        symbols: ["Timeout"]

Rules: exactly 20 questions; ground truth is file paths (symbols
optional); questions are frozen once Phase 2 begins; `scripts/eval.py`
appends dated result blocks below and old blocks are never edited.

## Results

(appended by scripts/eval.py)
