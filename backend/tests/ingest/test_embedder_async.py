"""Model inference must not run on the event loop (DECISIONS 2026-07-28).

``encode`` and ``score`` are synchronous torch forward passes. Called inline
from a coroutine they hold the loop for their whole duration, which stalls every
other request in the process — other answers, ``/health``, the SSE heartbeats —
not only the caller that asked for the search.

No real model is loaded here. The singletons are replaced with fakes that block
for a known time, which is the only property under test: *where* the blocking
happens.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

import pytest

from app.config import get_settings
from app.ingest import embedder


class BlockingEmbedder:
    """Blocks the calling thread, exactly as a real forward pass does."""

    dim = 4

    def __init__(self, seconds: float = 0.2) -> None:
        self.seconds = seconds
        self.threads: list[str] = []
        self.concurrent = 0
        self.max_concurrent = 0
        self._lock = threading.Lock()

    def encode(self, texts: list[str], batch_size: int = 64) -> list[list[float]]:
        with self._lock:
            self.concurrent += 1
            self.max_concurrent = max(self.max_concurrent, self.concurrent)
            self.threads.append(threading.current_thread().name)
        try:
            time.sleep(self.seconds)
            return [[0.0] * self.dim for _ in texts]
        finally:
            with self._lock:
                self.concurrent -= 1

    def token_len(self, text: str) -> int:
        return len(text)


class BlockingReranker:
    def __init__(self, seconds: float = 0.1) -> None:
        self.seconds = seconds
        self.threads: list[str] = []

    def score(self, query: str, passages: list[str]) -> list[float]:
        self.threads.append(threading.current_thread().name)
        time.sleep(self.seconds)
        return [1.0] * len(passages)


@pytest.fixture(autouse=True)
def clean_inference_state() -> Any:
    """Fresh singletons and a fresh thread pool per test."""
    embedder.shutdown_inference()
    yield
    embedder._embedder = None
    embedder._reranker = None
    embedder.shutdown_inference()


async def test_encoding_leaves_the_event_loop_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A heartbeat keeps ticking through a blocking encode. Inline, it would not."""
    monkeypatch.setattr(embedder, "_embedder", BlockingEmbedder(0.25))

    ticks = 0

    async def heartbeat() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    beat = asyncio.create_task(heartbeat())
    try:
        result = await embedder.encode_async(["how does auth work"])
    finally:
        beat.cancel()

    assert result == [[0.0, 0.0, 0.0, 0.0]]
    assert ticks >= 5, f"the loop was blocked during the encode (ticks={ticks})"


async def test_encoding_runs_on_the_inference_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = BlockingEmbedder(0.01)
    monkeypatch.setattr(embedder, "_embedder", fake)
    await embedder.encode_async(["x"])
    assert fake.threads and all(t.startswith("inference") for t in fake.threads)


async def test_reranking_also_leaves_the_loop_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The heavier of the two: a cross-encoder scores every candidate passage."""
    fake = BlockingReranker(0.01)
    monkeypatch.setattr(embedder, "_reranker", fake)
    scores = await embedder.rerank_async("q", ["a", "b", "c"])
    assert scores == [1.0, 1.0, 1.0]
    assert all(t.startswith("inference") for t in fake.threads)


async def test_concurrent_encodes_are_capped_by_the_pool_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unbounded threads on a 4-core box make every request slower, not just extras."""
    monkeypatch.setattr(get_settings(), "INFERENCE_THREADS", 2)
    fake = BlockingEmbedder(0.05)
    monkeypatch.setattr(embedder, "_embedder", fake)

    await asyncio.gather(*(embedder.encode_async([f"q{i}"]) for i in range(6)))
    assert fake.max_concurrent <= 2


async def test_empty_input_never_reaches_the_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = BlockingEmbedder(5.0)
    monkeypatch.setattr(embedder, "_embedder", fake)
    assert await embedder.encode_async([]) == []
    assert await embedder.rerank_async("q", []) == []
    assert fake.threads == []


def test_the_model_is_built_once_under_concurrent_first_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two inference threads racing a cold singleton must not load two models."""
    built = []

    class SlowToBuild:
        dim = 4

        def __init__(self, model_name: str, token: str | None = None) -> None:
            time.sleep(0.05)  # widen the window a naive check would race through
            built.append(model_name)

    monkeypatch.setattr(embedder, "_embedder", None)
    monkeypatch.setattr(embedder, "SentenceTransformerEmbedder", SlowToBuild)

    results: list[Any] = []
    threads = [
        threading.Thread(target=lambda: results.append(embedder.get_embedder()))
        for _ in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(built) == 1
    assert len({id(r) for r in results}) == 1
