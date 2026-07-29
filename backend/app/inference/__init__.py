"""The inference service (SPEC §16).

A separate process whose whole job is the two torch forward passes: embedding
and cross-encoder reranking. Run it with

    uv run uvicorn app.inference.main:service --port 8001

and point the API and/or the workers at it with ``INFERENCE_URL``.

It imports ``app.ingest.embedder`` — the one module allowed to know
sentence-transformers exists (CLAUDE.md rule 3) — so the model plumbing, the
thread pinning and the singleton locking are all shared with the in-process
path rather than reimplemented here.
"""
