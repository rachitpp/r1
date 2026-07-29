"""Inference service HTTP surface (SPEC §16.2).

Stateless and repo-agnostic: it sees text and returns numbers. It holds no
database handle, no user concept, and no notion of a snapshot — which is what
lets it be scaled, restarted, or moved to a GPU box without touching anything
else.

**Order is part of the contract.** ``vectors[i]`` corresponds to ``texts[i]``
and ``scores[i]`` to ``passages[i]``. A service that reordered its output would
mis-assign every embedding in a batch, and nothing downstream could detect it.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from pydantic import BaseModel, Field

from app.config import RERANK_PASSAGE_TOKENS, get_settings
from app.ingest.embedder import (
    encode_async,
    get_embedder,
    rerank_async,
    shutdown_inference,
)
from app.logging_setup import configure_logging

logger = logging.getLogger(__name__)

# Batch ceilings. The service is a shared resource: one caller asking for a
# hundred thousand texts in a single request would hold the model for minutes
# and stall every other caller, so a large job is the client's to chunk.
MAX_TEXTS = 512
MAX_PASSAGES = 256


class EmbedRequest(BaseModel):
    texts: list[str] = Field(min_length=1, max_length=MAX_TEXTS)
    batch_size: int = Field(default=64, ge=1, le=256)


class EmbedResponse(BaseModel):
    vectors: list[list[float]]
    dim: int


class TokenizeRequest(BaseModel):
    texts: list[str] = Field(min_length=1, max_length=MAX_TEXTS)


class TokenizeResponse(BaseModel):
    """Token counts under the *model's own* tokenizer.

    Exists because §2.5's oversize split is defined in model tokens, so a client
    that has no local model still needs the real count — a heuristic here would
    silently change chunk boundaries, and chunk boundaries are the corpus.
    """

    lengths: list[int]


class RerankRequest(BaseModel):
    query: str = Field(min_length=1)
    passages: list[str] = Field(min_length=1, max_length=MAX_PASSAGES)


class RerankResponse(BaseModel):
    scores: list[float]


service = FastAPI(title="Inference service", version="1")


@service.on_event("startup")
async def _startup() -> None:
    configure_logging()
    settings = get_settings()
    # Load eagerly. The whole reason this process exists is to hold the model,
    # so deferring the load would just move a 10-second stall onto the first
    # user request — the opposite of the point.
    embedder = get_embedder()
    logger.info(
        "inference service up | model=%s | dim=%d | rerank_passage_tokens=%d",
        settings.EMBEDDING_MODEL,
        embedder.dim,
        RERANK_PASSAGE_TOKENS,
    )


@service.on_event("shutdown")
async def _shutdown() -> None:
    shutdown_inference()


@service.get("/health")
async def health() -> dict[str, object]:
    """Liveness *and* identity: the model name is the thing worth checking.

    Two replicas on different `EMBEDDING_MODEL`s would return vectors from
    different spaces into one corpus, which no dimension check would catch and
    no retrieval metric would explain. Callers can compare this against their
    own configuration.
    """
    return {
        "ok": True,
        "model": get_settings().EMBEDDING_MODEL,
        "dim": get_embedder().dim,
    }


@service.post("/embed", response_model=EmbedResponse)
async def embed(body: EmbedRequest) -> EmbedResponse:
    vectors = await encode_async(body.texts, body.batch_size)
    return EmbedResponse(vectors=vectors, dim=get_embedder().dim)


@service.post("/tokenize", response_model=TokenizeResponse)
async def tokenize(body: TokenizeRequest) -> TokenizeResponse:
    embedder = get_embedder()
    return TokenizeResponse(lengths=[embedder.token_len(t) for t in body.texts])


@service.post("/rerank", response_model=RerankResponse)
async def rerank(body: RerankRequest) -> RerankResponse:
    scores = await rerank_async(body.query, body.passages)
    return RerankResponse(scores=scores)
