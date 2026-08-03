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

**Async callers must use** :func:`encode_async` / :func:`rerank_async`.
``encode`` and ``score`` are synchronous torch forward passes: called directly
from a coroutine they hold the event loop for their whole duration, which stalls
every other request in the process — other answers, ``/health``, the SSE
heartbeats — not just the caller. The async wrappers run them on a small
dedicated thread pool instead. Torch releases the GIL inside its kernels, so
this is real parallelism rather than bookkeeping, and the pool is deliberately
*small*: the constraint is cores, and unbounded threads on a 4-core box make
every request slower rather than only the extra ones.

The synchronous methods stay public and are the right thing for the ingest
worker, which is a separate process whose entire job is this work.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol

from app import metrics
from app.config import RERANK_PASSAGE_TOKENS, get_settings

logger = logging.getLogger(__name__)


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

        _pin_torch_threads()
        self._model = SentenceTransformer(model_name, token=token)
        # get_sentence_embedding_dimension() was renamed in sentence-transformers
        # 5.x; prefer the new name and fall back so either version works.
        get_dim = getattr(
            self._model,
            "get_embedding_dimension",
            self._model.get_sentence_embedding_dimension,
        )
        dim = get_dim()
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


# The service caps a request at 512 texts (§16.2); stay comfortably under it.
MAX_REMOTE_BATCH = 256


class HttpEmbedder:
    """``Embedder`` backed by the §16 inference service instead of a local model.

    Satisfies the same Protocol as :class:`SentenceTransformerEmbedder`, which is
    the entire reason this is a small change: CLAUDE.md rule 3 already confined
    sentence-transformers to this module, so the seam existed before the service
    did. Selected by setting ``INFERENCE_URL``; unset keeps the local model, so
    development and the CLIs work with nothing extra running.

    **Synchronous on purpose.** The Protocol is sync and
    :func:`encode_async` already runs it off the event loop, so this shares one
    code path with the local model rather than adding an async twin of every
    call site. The extra thread hop costs nothing next to a network round trip.

    ``dim`` is read from the service at construction. Trusting a configured
    value would let a service running a different ``EMBEDDING_MODEL`` write
    vectors from another space into the corpus — undetectable by any dimension
    check, and inexplicable in every retrieval metric afterwards.
    """

    def __init__(self, base_url: str, timeout_s: float) -> None:
        import httpx

        self._client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout_s)
        health = self._client.get("/health")
        health.raise_for_status()
        body = health.json()
        self.dim: int = int(body["dim"])
        logger.info(
            "using inference service at %s | model=%s | dim=%d",
            base_url,
            body.get("model"),
            self.dim,
        )

    def encode(self, texts: list[str], batch_size: int = 64) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        # Chunked to the service's own ceiling (§16.2 MAX_TEXTS). An ingest
        # embeds thousands of chunks; sending them as one request would be
        # refused, and holding the shared model for that long would stall every
        # other caller.
        for start in range(0, len(texts), MAX_REMOTE_BATCH):
            window = texts[start : start + MAX_REMOTE_BATCH]
            response = self._client.post(
                "/embed", json={"texts": window, "batch_size": batch_size}
            )
            response.raise_for_status()
            vectors.extend(response.json()["vectors"])
        return vectors

    def token_len(self, text: str) -> int:
        """Token count from the service (§16.3).

        One round trip per call, and the chunker calls this once per oversize
        check — ~1400 times on httpx. Correct but not fast: an ingest worker
        should run the **local** model (leave ``INFERENCE_URL`` unset there) and
        let the API be the remote client. Kept rather than raising, because a
        wrong-but-fast heuristic here would silently move chunk boundaries, and
        chunk boundaries are the corpus.
        """
        response = self._client.post("/tokenize", json={"texts": [text]})
        response.raise_for_status()
        return int(response.json()["lengths"][0])

    def close(self) -> None:
        self._client.close()


class Reranker:
    """Cross-encoder reranker wrapper (SPEC §5.3).

    ``max_length`` is fixed to ``RERANK_PASSAGE_TOKENS`` so the CrossEncoder
    itself handles passage truncation — callers pass full ``header + code``.
    """

    def __init__(
        self,
        model_name: str,
        token: str | None = None,
        *,
        trust_remote_code: bool = False,
    ) -> None:
        from sentence_transformers import CrossEncoder

        _pin_torch_threads()
        # `trust_remote_code` executes the model's own Python from the Hub at
        # load time. Threaded through rather than hardcoded either way: the one
        # credible code-trained cross-encoder needs it, and a deployment should
        # not inherit it. See `RERANKER_TRUST_REMOTE_CODE` for the trade.
        self._model = CrossEncoder(
            model_name,
            max_length=RERANK_PASSAGE_TOKENS,
            token=token,
            trust_remote_code=trust_remote_code,
        )

    def score(self, query: str, passages: list[str]) -> list[float]:
        """Return one relevance score per passage for ``query`` (order preserved)."""
        if not passages:
            return []
        scores = self._model.predict([(query, p) for p in passages])
        return [float(s) for s in scores]


def _pin_torch_threads() -> None:
    """Cap torch's intra-op threads before any model is built.

    Without this, torch sizes its own thread pool from the core count — so N
    concurrent forward passes on the inference pool each try to use every core,
    and the resulting oversubscription makes all of them slower than running one
    at a time would have been. ``INFERENCE_THREADS × TORCH_NUM_THREADS`` should
    land at or under the core count.

    Best-effort: torch refuses the call once parallel work has begun, and a
    thread-count hint is never worth failing a request over.
    """
    n = get_settings().TORCH_NUM_THREADS
    if n is None:
        return
    try:
        import torch

        torch.set_num_threads(n)
    except Exception as exc:  # noqa: BLE001 — a hint, not a requirement
        logger.debug("could not pin torch threads: %s", exc)


_embedder: SentenceTransformerEmbedder | HttpEmbedder | None = None
_reranker: Reranker | None = None

# Guards construction, not use. The models themselves are read-only once built
# and safe to call from several threads; building one twice would load a second
# copy of a 130 MB (or 2.4 GB) model into the same process, which is the failure
# this prevents now that two inference threads can race into a cold singleton.
_load_lock = threading.Lock()

_executor: ThreadPoolExecutor | None = None
_executor_lock = threading.Lock()


def get_embedder() -> SentenceTransformerEmbedder | HttpEmbedder:
    """Return the process-wide embedder singleton, built on first use.

    ``INFERENCE_URL`` selects the implementation (§16.3). Unset means the local
    model, so nothing about development, the CLIs, or a single-box deployment
    changes — the remote path is opt-in, not a new requirement.

    Note the inference service itself calls this and must never be configured
    with ``INFERENCE_URL`` pointing at itself, which would be an infinite
    delegation. Nothing enforces that; it is a deployment mistake, and the
    /health round trip in the constructor makes it fail loudly at startup rather
    than mysteriously under load.
    """
    global _embedder
    with _load_lock:
        if _embedder is None:
            settings = get_settings()
            if settings.INFERENCE_URL:
                _embedder = HttpEmbedder(
                    settings.INFERENCE_URL, settings.INFERENCE_TIMEOUT_S
                )
            else:
                _embedder = SentenceTransformerEmbedder(
                    settings.EMBEDDING_MODEL, token=settings.HF_TOKEN
                )
        return _embedder


def get_reranker() -> Reranker:
    """Return the process-wide reranker singleton, loading it on first use."""
    global _reranker
    with _load_lock:
        if _reranker is None:
            settings = get_settings()
            _reranker = Reranker(
                settings.RERANKER_MODEL,
                token=settings.HF_TOKEN,
                trust_remote_code=settings.RERANKER_TRUST_REMOTE_CODE,
            )
        return _reranker


def _get_executor() -> ThreadPoolExecutor:
    """The process-wide inference thread pool, created on first use.

    Bounded at ``INFERENCE_THREADS``. Work beyond that queues in the executor
    rather than spawning threads, which is the bound: an overloaded API answers
    slowly, it does not thrash.
    """
    global _executor
    with _executor_lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(
                max_workers=get_settings().INFERENCE_THREADS,
                thread_name_prefix="inference",
            )
        return _executor


def shutdown_inference() -> None:
    """Tear down the inference pool (API lifespan shutdown)."""
    global _executor
    with _executor_lock:
        if _executor is not None:
            _executor.shutdown(wait=False, cancel_futures=True)
            _executor = None


async def encode_async(texts: list[str], batch_size: int = 64) -> list[list[float]]:
    """:meth:`Embedder.encode`, off the event loop.

    The model load itself happens inside the worker thread too — it is the one
    call that can take ten seconds, and the whole point of this function is that
    no such thing runs where it can stop the loop.
    """
    if not texts:
        return []
    loop = asyncio.get_running_loop()
    metrics.inference_queue_depth.inc()
    try:
        # Timed around the await, so the number is what the caller waited —
        # queueing behind other inference included. That is the latency a user
        # feels; pure forward-pass time is not.
        with metrics.inference_duration.time(op="embed"):
            return await loop.run_in_executor(
                _get_executor(), lambda: get_embedder().encode(texts, batch_size)
            )
    finally:
        metrics.inference_queue_depth.inc(-1)


async def rerank_async(query: str, passages: list[str]) -> list[float]:
    """:meth:`Reranker.score`, off the event loop.

    Heavier than embedding by a wide margin — a cross-encoder scores every
    (query, passage) pair — which is why the default pipeline leaves rerank off
    (SPEC §5.3) and why running it inline would be the worst offender of the two.
    """
    if not passages:
        return []
    loop = asyncio.get_running_loop()
    metrics.inference_queue_depth.inc()
    try:
        with metrics.inference_duration.time(op="rerank"):
            return await loop.run_in_executor(
                _get_executor(), lambda: get_reranker().score(query, passages)
            )
    finally:
        metrics.inference_queue_depth.inc(-1)
