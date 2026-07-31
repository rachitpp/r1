"""Auto-generated repo overview (SPEC §19).

The "start here" guide a newcomer sees the moment indexing finishes: what the
project does, how it is laid out, where execution starts, and what to read
first — every claim carrying a `file:line` citation.

**Gather deterministically, synthesise once.** This does *not* run the §7.2
agent loop. The loop is the right tool for a question nobody anticipated; an
overview is the same six questions for every repo, and all six have exact
answers in the symbol graph. So the facts are assembled by SQL and handed to a
**single** model call.

Three things follow from that, and they are the reason for the design rather
than side effects:

* **Cost.** One request per snapshot, not the eight a loop would spend. The
  tuning provider's free tier is 20 requests/day/model (`app/agent/model.py`),
  so a loop here would make a handful of repo pages a day the entire budget.
* **Reproducibility.** The input is a pure function of an immutable snapshot
  (§14.3), so two generations differ only by model sampling — and the stored
  row means there is normally only ever one.
* **Coverage.** A loop capped at eight calls sees whatever its first search
  happened to surface. The rollup sees the whole graph, ranked, every time.

**What is deliberately absent: "how to run it".** `filters.py` keeps `*.py`
only, so no README, `pyproject.toml`, or CI config is in the corpus. An
overview section on installation would be the model recalling how projects like
this one usually work — exactly the failure this whole product exists to avoid.
The prompt forbids it rather than leaving it to chance.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

if TYPE_CHECKING:
    # Typing only — importing this at runtime pulls transformers and torch,
    # which is what keeps an API replica off both (SPEC §16.1).
    from langchain_core.language_models.chat_models import BaseChatModel

import asyncpg

from app.agent.citations import parse_citations, validate_citations
from app.agent.prompts import OVERVIEW_SYSTEM, overview_brief
from app.config import (
    ARCH_MAX_NODES,
    OVERVIEW_MAX_API_SYMBOLS,
    OVERVIEW_MAX_ENTRY_POINTS,
    OVERVIEW_MAX_KEY_SYMBOLS,
    OVERVIEW_MAX_MODULES,
)
from app.db import queries
from app.exceptions import AgentError

logger = logging.getLogger(__name__)


async def gather_facts(
    conn: asyncpg.Connection, snapshot_id: UUID
) -> dict[str, Any]:
    """Everything the synthesis prompt is allowed to know, straight from SQL.

    Returned as a plain dict so it can be asserted on in a test and rendered
    into a prompt without a model being involved in either. Nothing here is a
    guess: every entry names a real file and a real line range.
    """
    source = await queries.source_of(conn, snapshot_id)
    repo = await queries.get_repo(conn, snapshot_id)
    if source is None or repo is None:
        raise AgentError(f"snapshot {snapshot_id} not found")

    # `n_lines` as well as `path`, because every module in the brief needs a
    # citable range. Without one the model does not decline to cite — it writes
    # a placeholder (`[httpx/_models.py:1-?]`, observed live), which fails
    # validation and leaves the claim uncited *and* the markdown looking broken.
    file_rows = await conn.fetch(
        "SELECT path, n_lines FROM files WHERE snapshot_id = $1 ORDER BY path",
        snapshot_id,
    )
    paths = [str(r["path"]) for r in file_rows]
    n_lines_of = {str(r["path"]): int(r["n_lines"]) for r in file_rows}
    modules = await queries.module_nodes(
        conn, snapshot_id, include_tests=False, limit=ARCH_MAX_NODES
    )
    entry_points = await queries.entry_point_candidates(
        conn, snapshot_id, OVERVIEW_MAX_ENTRY_POINTS
    )
    api = await queries.public_api_symbols(
        conn, snapshot_id, OVERVIEW_MAX_API_SYMBOLS
    )
    key_symbols = await queries.most_referenced_symbols(
        conn, snapshot_id, OVERVIEW_MAX_KEY_SYMBOLS
    )

    return {
        "name": str(source["name"]),
        "url": str(source["url"]),
        "commit": repo["head_sha"],
        "n_files": len(paths),
        "top_dirs": sorted({p.split("/")[0] for p in paths if "/" in p}),
        # Only the head of the ranking reaches the prompt: everything here is
        # tokens, and module 40 of 200 informs nothing.
        "modules": [
            {
                "path": p,
                "n_symbols": n,
                "fan_in": fi,
                "fan_out": fo,
                # A whole-file range, which is the honest span for a claim about
                # a module as a unit ("this is the centre of the codebase").
                "start_line": 1,
                "end_line": n_lines_of.get(p, 1),
            }
            for p, n, fi, fo in modules[:OVERVIEW_MAX_MODULES]
        ],
        "n_modules_total": len(modules),
        "entry_points": [
            {
                "path": str(r["path"]),
                "fan_in": int(r["fan_in"]),
                "fan_out": int(r["fan_out"]),
                "named": bool(r["named"]),
                "start_line": int(r["start_line"]),
                "end_line": int(r["end_line"]),
            }
            for r in entry_points
        ],
        "public_api": [
            {
                "qualname": str(r["qualname"]),
                "kind": str(r["kind"]),
                "file_path": str(r["file_path"]),
                "start_line": int(r["start_line"]),
                "end_line": int(r["end_line"]),
            }
            for r in api
        ],
        "key_symbols": [
            {
                "qualname": str(r["qualname"]),
                "kind": str(r["kind"]),
                "file_path": str(r["file_path"]),
                "start_line": int(r["start_line"]),
                "end_line": int(r["end_line"]),
                "refs": int(r["refs"]),
            }
            for r in key_symbols
        ],
    }


async def generate_overview(
    model: BaseChatModel,
    conn: asyncpg.Connection,
    snapshot_id: UUID,
) -> tuple[str, list[dict[str, Any]]]:
    """Synthesise the overview. Returns ``(markdown, validated_citations)``.

    One model call. Citations are parsed and validated against the ``files``
    table exactly as a chat answer's are (§7.5), so a fabricated path is dropped
    before anything is stored — an overview is read as reference material and
    has a longer half-life than an answer, which makes a bad citation worse
    here, not better.
    """
    facts = await gather_facts(conn, snapshot_id)
    brief = overview_brief(facts)

    from langchain_core.messages import HumanMessage, SystemMessage

    response = await model.ainvoke(
        [SystemMessage(content=OVERVIEW_SYSTEM), HumanMessage(content=brief)]
    )
    body = (
        response.text()
        if callable(getattr(response, "text", None))
        else str(response.content)
    ).strip()
    if not body:
        raise AgentError("the model returned an empty overview")

    citations = await validate_citations(conn, snapshot_id, parse_citations(body))
    logger.info(
        "overview for %s: %d chars, %d validated citations",
        snapshot_id,
        len(body),
        len(citations),
    )
    return body, [dict(c) for c in citations]


async def run_overview_job(
    model: BaseChatModel, conn: asyncpg.Connection, snapshot_id: UUID, model_name: str
) -> None:
    """Generate and store, recording failure on the row (§19.4).

    Mirrors `worker.ingest_repo`: the generator raises, and the caller that owns
    the row decides what a failure looks like to a UI.
    """
    body, citations = await generate_overview(model, conn, snapshot_id)
    await queries.finish_overview(
        conn,
        snapshot_id,
        body=body,
        citations=json.dumps(citations),
        model=model_name,
    )
