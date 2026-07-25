"""Embeddings and reranking (SPEC §4, §5.3).

This is the **only** module that imports ``sentence_transformers`` (CLAUDE.md
hard rule 3), including the CrossEncoder used for reranking. Everything else
goes through the ``get_embedder()`` / ``get_reranker()`` factory singletons
below; ``retrieval/`` imports those factories, never the library.

The ``sentence_transformers`` import is deferred into the constructors so that
merely importing this module (or ``retrieval``, or the pure helpers/tests that
depend on it) does not drag in torch. Both models load once per process
(lazily, on first ``get_*`` call) and are cached for the lifetime of the
process — never per request. Model ids and the optional HF token come from
``app.config``.
"""

from __future__ import annotations

from typing import Protocol

from app.config import RERANK_PASSAGE_TOKENS, get_settings


class Embedder(Protocol):
    """The embedding interface the rest of the app depends on (SPEC §4).

    Also satisfies :class:`app.ingest.tokens.TokenCounter` via ``token_len``,
    so the real model tokenizer can drive oversize-split decisions in Phase 2.
    """

    dim: int

    def encode(self, texts: list[str], batch_size: int = 64) -> list[list[float]]: ...

    def token_len(self, text: str) -> int: ...


class SentenceTransformerEmbedder:
    """``Embedder`` backed by a sentence-transformers bi-encoder."""

    def __init__(self, model_name: str, token: str | None = None) -> None:
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model_name, token=token)
        dim = self._model.get_sentence_embedding_dimension()
        if dim is None:  # pragma: no cover — every real ST model reports a dim
            raise RuntimeError(f"model {model_name!r} did not report an embedding dim")
        self.dim: int = dim

    def encode(self, texts: list[str], batch_size: int = 64) -> list[list[float]]:
        """Encode ``texts`` to unit-normalized vectors (cosine distance, SPEC §4)."""
        if not texts:
            return []
        vectors = self._model.encode(
            texts,
            batch_size=batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return [[float(x) for x in row] for row in vectors]

    def token_len(self, text: str) -> int:
        """Token count under the model's own tokenizer (incl. special tokens)."""
        ids = self._model.tokenizer(text, add_special_tokens=True, truncation=False)
        return len(ids["input_ids"])


class Reranker:
    """Cross-encoder reranker wrapper (SPEC §5.3).

    ``max_length`` is fixed to ``RERANK_PASSAGE_TOKENS`` so the CrossEncoder
    itself handles passage truncation — callers pass full ``header + code``.
    """

    def __init__(self, model_name: str, token: str | None = None) -> None:
        from sentence_transformers import CrossEncoder

        self._model = CrossEncoder(
            model_name, max_length=RERANK_PASSAGE_TOKENS, token=token
        )

    def score(self, query: str, passages: list[str]) -> list[float]:
        """Return one relevance score per passage for ``query`` (order preserved)."""
        if not passages:
            return []
        scores = self._model.predict([(query, p) for p in passages])
        return [float(s) for s in scores]


_embedder: SentenceTransformerEmbedder | None = None
_reranker: Reranker | None = None


def get_embedder() -> SentenceTransformerEmbedder:
    """Return the process-wide embedder singleton, loading it on first use."""
    global _embedder
    if _embedder is None:
        settings = get_settings()
        _embedder = SentenceTransformerEmbedder(
            settings.EMBEDDING_MODEL, token=settings.HF_TOKEN
        )
    return _embedder


def get_reranker() -> Reranker:
    """Return the process-wide reranker singleton, loading it on first use."""
    global _reranker
    if _reranker is None:
        settings = get_settings()
        _reranker = Reranker(settings.RERANKER_MODEL, token=settings.HF_TOKEN)
    return _reranker
