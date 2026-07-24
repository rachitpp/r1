"""Ingestion pipeline: clone -> filter -> parse -> chunk.

Phase 1 is CLI-only (no DB, embeddings, or HTTP). The public surface is the
``python -m app.ingest.cli`` entrypoint plus the dataclasses each stage
returns; see SPEC §2.
"""
