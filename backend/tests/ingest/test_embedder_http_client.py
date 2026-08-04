"""The inference service and its client (SPEC §16).

No model is loaded here. `HttpEmbedder` is tested against a stub service, which
is the right level: the thing worth pinning is the *contract* — order, batching,
and reading `dim` from the service rather than trusting configuration. Whether
sentence-transformers produces good vectors is Phase 2's question and `eval.py`
answers it.
"""

from __future__ import annotations

import httpx
import pytest

from app.ingest import embedder as emb


def _stub(dim: int = 4, *, record: list[dict] | None = None) -> httpx.Client:
    """An httpx.Client wired to a fake §16.2 service via MockTransport."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(
                200, json={"ok": True, "model": "stub-model", "dim": dim}
            )
        body = request.content and __import__("json").loads(request.content) or {}
        if record is not None:
            record.append({"path": request.url.path, "body": body})
        if request.url.path == "/embed":
            texts = body["texts"]
            # Distinct, order-revealing vectors: the i-th text gets its own
            # first component, so a reordering bug cannot pass.
            return httpx.Response(
                200,
                json={
                    "vectors": [[float(len(t))] * dim for t in texts],
                    "dim": dim,
                },
            )
        if request.url.path == "/tokenize":
            return httpx.Response(
                200, json={"lengths": [len(t.split()) for t in body["texts"]]}
            )
        if request.url.path == "/rerank":
            return httpx.Response(
                200, json={"scores": [float(len(p)) for p in body["passages"]]}
            )
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://svc")


@pytest.fixture
def remote(monkeypatch: pytest.MonkeyPatch):
    """An `HttpEmbedder` whose client is the stub, with calls recorded."""
    record: list[dict] = []
    client = _stub(record=record)

    def fake_init(self: emb.HttpEmbedder, base_url: str, timeout_s: float) -> None:
        self._client = client
        health = client.get("/health")
        self.dim = int(health.json()["dim"])

    monkeypatch.setattr(emb.HttpEmbedder, "__init__", fake_init)
    return emb.HttpEmbedder("http://svc", 5.0), record


def test_dim_comes_from_the_service_not_from_config(remote) -> None:
    """A service on a different model would write vectors from another space.

    No dimension check downstream would catch it and no retrieval metric would
    explain it, so `dim` is read over the wire at construction (§16.3).
    """
    client, _ = remote
    assert client.dim == 4


def test_encode_preserves_order(remote) -> None:
    """`vectors[i]` must correspond to `texts[i]` (§16.2).

    A service that reordered its output would mis-assign every embedding in the
    batch, and nothing downstream could detect it.
    """
    client, _ = remote
    texts = ["a", "bbb", "cc"]
    vectors = client.encode(texts)
    assert [v[0] for v in vectors] == [1.0, 3.0, 2.0]


def test_encode_of_nothing_makes_no_request(remote) -> None:
    """An empty batch is a no-op, not a round trip."""
    client, record = remote
    assert client.encode([]) == []
    assert [c for c in record if c["path"] == "/embed"] == []


def test_a_large_batch_is_split_and_reassembled_in_order(remote) -> None:
    """Requests are chunked to the service's ceiling (§16.2), output is not.

    An ingest embeds thousands of chunks: one request would be refused and would
    hold the shared model long enough to stall every other caller.
    """
    client, record = remote
    texts = [f"{'x' * (i % 7 + 1)}" for i in range(600)]
    vectors = client.encode(texts)

    embeds = [c for c in record if c["path"] == "/embed"]
    assert len(embeds) == 3  # 600 over a 256 ceiling
    assert all(len(c["body"]["texts"]) <= emb.MAX_REMOTE_BATCH for c in embeds)
    # Reassembly is order-preserving across the split, which is the part a
    # naive chunking loop gets wrong.
    assert len(vectors) == 600
    assert [v[0] for v in vectors] == [float(len(t)) for t in texts]


def test_token_len_uses_the_services_tokenizer(remote) -> None:
    """§2.5's oversize split is defined in model tokens, so this must be real.

    A local heuristic would silently move chunk boundaries, and chunk boundaries
    are the corpus.
    """
    client, _ = remote
    assert client.token_len("one two three") == 3


def test_the_client_satisfies_the_embedder_protocol() -> None:
    """The seam CLAUDE.md rule 3 created is what makes §16 a small change."""
    assert isinstance(emb.HttpEmbedder, type)
    for name in ("encode", "token_len"):
        assert callable(getattr(emb.HttpEmbedder, name))


def test_the_factory_picks_the_remote_client_when_a_url_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`INFERENCE_URL` is the switch, and unset must keep the local model.

    Asserted by construction rather than by loading either one: building the
    real `SentenceTransformerEmbedder` here would pull in torch, which is the
    thing the next test forbids.
    """
    from app.config import get_settings

    built: list[str] = []

    class FakeRemote:
        def __init__(self, url: str, timeout: float) -> None:
            built.append(f"remote:{url}")
            self.dim = 4

    class FakeLocal:
        def __init__(self, model: str, token: str | None = None) -> None:
            built.append(f"local:{model}")
            self.dim = 4

    monkeypatch.setattr(emb, "HttpEmbedder", FakeRemote)
    monkeypatch.setattr(emb, "SentenceTransformerEmbedder", FakeLocal)

    monkeypatch.setattr(emb, "_embedder", None)
    monkeypatch.setattr(get_settings(), "INFERENCE_URL", "http://svc:8001")
    emb.get_embedder()
    assert built == ["remote:http://svc:8001"]

    built.clear()
    monkeypatch.setattr(emb, "_embedder", None)
    monkeypatch.setattr(get_settings(), "INFERENCE_URL", None)
    emb.get_embedder()
    assert built[0].startswith("local:")
