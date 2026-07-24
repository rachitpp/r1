"""`/health` must return 200 with no services running.

Uses httpx ASGITransport to call the app in-process. The lifespan tolerates a
missing database, so this passes without Postgres or Redis.
"""

from __future__ import annotations

import httpx
from httpx import ASGITransport

from app.main import app


async def test_health_ok() -> None:
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
