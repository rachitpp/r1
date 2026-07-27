"""`/health` must return 200 with no services running.

Uses httpx ASGITransport to call the app in-process. The lifespan tolerates a
missing database, so this passes without Postgres or Redis.

The embedder warm-up (Phase 4 Reconciliation 3) is stubbed out: loading
bge-small takes ~18 seconds, and this test is about the liveness probe, not about
sentence-transformers. The warm-up's own failure tolerance is a one-line
try/except in the lifespan, not something worth 18 seconds per run.
"""

from __future__ import annotations

import httpx
import pytest
from httpx import ASGITransport

from app import main
from app.main import app


async def test_health_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "get_embedder", lambda: None)
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
