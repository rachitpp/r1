"""HTTP routes (SPEC §8). Thin: parse, delegate, shape the response.

Split from a single 1,165-line module, one module per resource. The
aggregate ``router`` below is what ``app.main`` includes, and the
sub-routers are registered in the order the routes were originally
declared — FastAPI matches in registration order, so the order is part of
the behaviour, not a style choice.

No route clones, parses, or embeds anything (CLAUDE.md hard rule 1) —
``POST /repos`` enqueues an ARQ job and returns. Failures are raised as
typed exceptions from :mod:`app.exceptions` and mapped to status codes by
the handlers in :mod:`app.api.errors`; no route builds an
``HTTPException`` itself.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import (
    chat,
    conversations,
    dependencies,
    graph,
    history,
    ops,
    overview,
    repos,
    sharing,
    snapshots,
)
from app.api.routes._common import chat_slots

router = APIRouter()
router.include_router(ops.router)
router.include_router(repos.router)
router.include_router(graph.router)
router.include_router(overview.router)
router.include_router(snapshots.router)
router.include_router(dependencies.router)
router.include_router(history.router)
router.include_router(conversations.router)
router.include_router(sharing.router)
router.include_router(chat.router)

__all__ = ["chat_slots", "router"]
