"""Retrieval package. All feature code retrieves via ``hybrid_search`` (rule 2)."""

from __future__ import annotations

from app.retrieval.hybrid import SearchHit, hybrid_search, search

__all__ = ["SearchHit", "hybrid_search", "search"]
