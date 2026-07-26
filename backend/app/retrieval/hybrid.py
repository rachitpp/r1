"""Hybrid retrieval: RRF fusion + exact-symbol injection + rerank (SPEC §5).

``hybrid_search(repo_id, query, k=SEARCH_K)`` is the single public entry point
for feature code (CLAUDE.md hard rule 2) and runs the full production pipeline
(vector ⊕ FTS fused with RRF, symbol injection, cross-encoder rerank).

The mode-parameterized ``search(conn, ...)`` underneath is what the Phase 2
diagnostics (``scripts/eval.py``, ``scripts/debug_search.py``) call to isolate a
single leg — vector-only, FTS-only, or fusion-without-rerank. Those are tools,
not features, so running one leg there does not violate rule 2; feature code
always goes through ``hybrid_search``.
"""

from __future__ import annotations

import re
from typing import Literal, TypedDict
from uuid import UUID

import asyncpg

from app.config import FTS_K, RRF_K, SEARCH_K, VEC_K, get_settings
from app.db.pool import close_pool, create_pool
from app.ingest.embedder import get_embedder, get_reranker

Mode = Literal["vector", "fts", "hybrid", "hybrid+rerank"]
MODES: tuple[Mode, ...] = ("vector", "fts", "hybrid", "hybrid+rerank")

# Fusion returns at most this many candidates (SPEC §5.1 outer LIMIT / §5.3
# "fusion top-40"); injection may add up to INJECT_LIMIT more before rerank.
FUSION_LIMIT = 40
INJECT_LIMIT = 10
PREVIEW_CHARS = 200

# Retrieval targets implementation by default (SPEC §5.4). Test chunks stay in
# the corpus but are filtered out of every candidate-producing query unless
# ``include_tests=True``. Appended to a WHERE clause that already has a
# predicate; carries no bind parameter.
_NO_TESTS = " AND NOT is_test"


def _test_filter(include_tests: bool, *, alias: str = "") -> str:
    """SQL fragment excluding test chunks, or empty when they are included."""
    if include_tests:
        return ""
    return f" AND NOT {alias}is_test" if alias else _NO_TESTS


class SearchHit(TypedDict):
    """One retrieval result (SPEC §5.3). ``score`` semantics vary by mode:
    cosine similarity (vector), ts_rank (fts), RRF (hybrid), cross-encoder
    logit (hybrid+rerank)."""

    chunk_id: int
    file_path: str
    start_line: int
    end_line: int
    symbol: str | None
    kind: str
    score: float
    preview: str


def rrf_fuse(rank_lists: list[list[int]], k: int = RRF_K) -> list[tuple[int, float]]:
    """Reciprocal-rank fusion over per-signal ranked id lists (SPEC §5.1 formula).

    Each list is ranked best-first (rank 1 = index 0); an id's score is the sum
    of ``1/(k + rank)`` across the lists it appears in. Returns ``(id, score)``
    sorted by score descending.

    Production fuses in a single SQL statement (see :func:`_fusion`); this pure
    mirror of the same formula exists for unit tests and any non-SQL caller.
    """
    scores: dict[int, float] = {}
    for ids in rank_lists:
        for rank, cid in enumerate(ids, start=1):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda t: (-t[1], t[0]))


def qualname_matches(qualname: str | None, short_name: str) -> bool:
    """EVAL symbol-match rule: ``qualname`` equals ``short_name`` or ends with
    ``"." + short_name`` (e.g. ``Timeout`` matches ``httpx._config.Timeout``)."""
    if qualname is None:
        return False
    return qualname == short_name or qualname.endswith(f".{short_name}")


# ---------------------------------------------------------------------------
# Exact-symbol injection (SPEC §5.2, Reconciliation 1)
# ---------------------------------------------------------------------------

# A word or dotted word: `verify_token`, `DigestAuth`, `httpx.get`, `Timeout`.
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")


def _is_camel_case(token: str) -> bool:
    """Whether ``token`` is CamelCase: an uppercase letter at a **non-initial**
    position, plus at least one lowercase letter.

    The non-initial requirement is what separates an identifier from an ordinary
    sentence-initial capital: ``How``/``When``/``Where`` fail (their only capital
    is at index 0), while ``BasicAuth`` and ``URLPattern`` pass. Pure acronyms
    (``URL``, ``HTTP``) fail for want of a lowercase letter — acceptable, since
    FTS already covers bare acronyms and injecting them would add noise.
    """
    return any(c.isupper() for c in token[1:]) and any(c.islower() for c in token)


def _looks_like_identifier(token: str) -> bool:
    """Keep tokens with an underscore, a dot, or CamelCase shape.

    Plain lowercase words (`get`, `urlparse`) are dropped — they are handled by
    FTS and would only add noise here.
    """
    if "_" in token or "." in token:
        return True
    return _is_camel_case(token)


def extract_identifiers(query: str) -> list[str]:
    """Identifier-like tokens from ``query``, de-duplicated, order preserved."""
    seen: set[str] = set()
    out: list[str] = []
    for token in _IDENT_RE.findall(query):
        if _looks_like_identifier(token) and token not in seen:
            seen.add(token)
            out.append(token)
    return out


async def _inject_symbol_ids(
    conn: asyncpg.Connection, repo_id: UUID, query: str, *, include_tests: bool = False
) -> list[int]:
    """Chunk ids whose symbol matches a query identifier (SPEC §5.2).

    Match rule (Reconciliation 1): ``symbol = name`` OR ``symbol LIKE '%.'||name``
    against ``chunks.symbol`` (the symbols table does not exist until Phase 3).
    Test chunks are excluded unless ``include_tests`` (SPEC §5.4) — otherwise a
    query naming a symbol would inject its test alongside the implementation.
    """
    names = extract_identifiers(query)
    if not names:
        return []
    patterns = [f"%.{name}" for name in names]
    rows = await conn.fetch(
        f"""
        SELECT id FROM chunks
        WHERE repo_id = $1
          AND (symbol = ANY($2::text[]) OR symbol LIKE ANY($3::text[]))
          {_test_filter(include_tests)}
        LIMIT $4
        """,
        repo_id,
        names,
        patterns,
        INJECT_LIMIT,
    )
    return [int(r["id"]) for r in rows]


# ---------------------------------------------------------------------------
# Single-signal legs and RRF fusion (SPEC §5.1)
# ---------------------------------------------------------------------------


def _fts_query_sql(param: str) -> str:
    """SQL expression building the FTS tsquery from bind ``param`` (SPEC §5.1).

    ``plainto_tsquery`` strips english stopwords and stems, but ANDs every term —
    unsatisfiable for a full NL question over code, so the FTS leg returned zero
    rows and hybrid collapsed to vector-only (see DECISIONS 2026-07-25, FTS
    finding). We OR the same content lexemes instead by swapping ``&`` for ``|``,
    making FTS a lexical *recall* signal. Exact-symbol matching stays with §5.2
    injection, not FTS. Returns an empty tsquery (matches nothing, no error) when
    the query is all stopwords. The fragment carries only a placeholder — the
    query text always arrives as a bind parameter.
    """
    return (
        "to_tsquery('english', "
        f"replace(plainto_tsquery('english', {param})::text, ' & ', ' | '))"
    )


async def _vector_leg(
    conn: asyncpg.Connection,
    repo_id: UUID,
    qvec: list[float],
    limit: int,
    *,
    include_tests: bool = False,
) -> list[tuple[int, float]]:
    """Top-``limit`` chunk ids by cosine similarity, with similarity scores."""
    rows = await conn.fetch(
        f"""
        SELECT id, 1 - (embedding <=> $1) AS score
        FROM chunks WHERE repo_id = $2{_test_filter(include_tests)}
        ORDER BY embedding <=> $1
        LIMIT $3
        """,
        qvec,
        repo_id,
        limit,
    )
    return [(int(r["id"]), float(r["score"])) for r in rows]


async def _fts_leg(
    conn: asyncpg.Connection,
    repo_id: UUID,
    query: str,
    limit: int,
    *,
    include_tests: bool = False,
) -> list[tuple[int, float]]:
    """Top-``limit`` chunk ids by FTS ts_rank, with rank scores."""
    rows = await conn.fetch(
        f"""
        SELECT c.id, ts_rank(c.tsv, q) AS score
        FROM chunks c, {_fts_query_sql("$1")} AS q
        WHERE c.repo_id = $2 AND c.tsv @@ q
              {_test_filter(include_tests, alias="c.")}
        ORDER BY score DESC
        LIMIT $3
        """,
        query,
        repo_id,
        limit,
    )
    return [(int(r["id"]), float(r["score"])) for r in rows]


async def _fusion(
    conn: asyncpg.Connection,
    repo_id: UUID,
    qvec: list[float],
    query: str,
    *,
    include_tests: bool = False,
) -> list[tuple[int, float]]:
    """RRF-fuse the vector and FTS legs in one SQL statement (SPEC §5.1).

    Must run inside a transaction that has set ``hnsw.ef_search`` — the HNSW
    index applies the ``repo_id`` filter post-scan, and the default ef_search
    starves filtered results on multi-repo databases.

    Both CTEs apply the §5.4 test filter, so exclusion happens *before* the
    per-leg LIMIT — test chunks never consume a top-40 slot that implementation
    could have taken.
    """
    no_tests = _test_filter(include_tests)
    rows = await conn.fetch(
        f"""
        WITH vec AS (
          SELECT id, ROW_NUMBER() OVER (ORDER BY embedding <=> $1) AS rnk
          FROM chunks WHERE repo_id = $2{no_tests}
          ORDER BY embedding <=> $1 LIMIT $4
        ),
        fts AS (
          SELECT id, ROW_NUMBER() OVER (ORDER BY ts_rank(tsv, q) DESC) AS rnk
          FROM chunks, {_fts_query_sql("$3")} AS q
          WHERE repo_id = $2 AND tsv @@ q{no_tests}
          ORDER BY ts_rank(tsv, q) DESC LIMIT $5
        )
        SELECT COALESCE(v.id, f.id) AS chunk_id,
               COALESCE(1.0/($6 + v.rnk), 0) + COALESCE(1.0/($6 + f.rnk), 0) AS rrf
        FROM vec v FULL OUTER JOIN fts f ON v.id = f.id
        ORDER BY rrf DESC
        LIMIT $7
        """,
        qvec,
        repo_id,
        query,
        VEC_K,
        FTS_K,
        RRF_K,
        FUSION_LIMIT,
    )
    return [(int(r["chunk_id"]), float(r["rrf"])) for r in rows]


# ---------------------------------------------------------------------------
# Row fetch + hit assembly
# ---------------------------------------------------------------------------

_ROW_COLUMNS = "id, file_path, symbol, kind, start_line, end_line, header, code"


async def _fetch_rows(
    conn: asyncpg.Connection, ids: list[int]
) -> dict[int, asyncpg.Record]:
    if not ids:
        return {}
    rows = await conn.fetch(
        f"SELECT {_ROW_COLUMNS} FROM chunks WHERE id = ANY($1::bigint[])", ids
    )
    return {int(r["id"]): r for r in rows}


def _hit(row: asyncpg.Record, score: float) -> SearchHit:
    return {
        "chunk_id": int(row["id"]),
        "file_path": str(row["file_path"]),
        "start_line": int(row["start_line"]),
        "end_line": int(row["end_line"]),
        "symbol": row["symbol"],
        "kind": str(row["kind"]),
        "score": float(score),
        "preview": str(row["code"])[:PREVIEW_CHARS].strip(),
    }


async def _set_ef_search(conn: asyncpg.Connection) -> None:
    await conn.execute("SET LOCAL hnsw.ef_search = 100")


# ---------------------------------------------------------------------------
# Public search
# ---------------------------------------------------------------------------


async def search(
    conn: asyncpg.Connection,
    repo_id: UUID,
    query: str,
    *,
    k: int = SEARCH_K,
    mode: Mode = "hybrid+rerank",
    include_tests: bool = False,
) -> list[SearchHit]:
    """Retrieve the top-``k`` chunks for ``query`` under the given ``mode``.

    ``hybrid+rerank`` is the production pipeline; the other modes exist for the
    eval/debug diagnostics. Vector and hybrid modes embed the query; FTS does
    not — and does not load the embedder, so ``--mode fts`` runs model-free.
    Only ``hybrid+rerank`` performs symbol injection and reranking.

    ``include_tests`` (default False) is the SPEC §5.4 corpus condition: test
    chunks are excluded from every leg and from injection. Passing True restores
    the shadowed condition and exists to keep that counterfactual measurable.
    """
    if mode == "fts":
        scored = await _fts_leg(conn, repo_id, query, k, include_tests=include_tests)
        rows = await _fetch_rows(conn, [i for i, _ in scored])
        return [_hit(rows[i], s) for i, s in scored if i in rows]

    qvec = get_embedder().encode([query])[0]

    if mode == "vector":
        async with conn.transaction():
            await _set_ef_search(conn)
            scored = await _vector_leg(
                conn, repo_id, qvec, k, include_tests=include_tests
            )
        rows = await _fetch_rows(conn, [i for i, _ in scored])
        return [_hit(rows[i], s) for i, s in scored if i in rows]

    # hybrid / hybrid+rerank both start from RRF fusion.
    async with conn.transaction():
        await _set_ef_search(conn)
        fused = await _fusion(conn, repo_id, qvec, query, include_tests=include_tests)

    if mode == "hybrid":
        rows = await _fetch_rows(conn, [i for i, _ in fused])
        ordered = [_hit(rows[i], s) for i, s in fused if i in rows]
        return ordered[:k]

    # hybrid+rerank: fusion ∪ injection, deduped, cross-encoder reranked.
    injected = await _inject_symbol_ids(
        conn, repo_id, query, include_tests=include_tests
    )
    pool_ids: list[int] = list(dict.fromkeys([i for i, _ in fused] + injected))
    rows = await _fetch_rows(conn, pool_ids)
    pool_ids = [i for i in pool_ids if i in rows]
    if not pool_ids:
        return []
    passages = [f"{rows[i]['header']}\n{rows[i]['code']}" for i in pool_ids]
    scores = get_reranker().score(query, passages)
    ranked = sorted(zip(pool_ids, scores, strict=True), key=lambda t: t[1], reverse=True)
    return [_hit(rows[i], s) for i, s in ranked[:k]]


async def hybrid_search(
    repo_id: UUID, query: str, k: int = SEARCH_K, *, include_tests: bool = False
) -> list[SearchHit]:
    """The single public retrieval entry point (SPEC §5.3, CLAUDE.md rule 2).

    Runs the full production pipeline (hybrid fusion + symbol injection +
    rerank) and manages its own pooled connection. Feature code (agent, API)
    calls this; it never touches the single-signal legs directly.

    Retrieval targets implementation by default: test chunks are flagged at
    ingest and filtered here (SPEC §5.4). They remain in the ``files`` table, so
    ``read_file`` and ``list_directory`` still see them.
    """
    settings = get_settings()
    pool = await create_pool(settings.DATABASE_URL)
    try:
        async with pool.acquire() as conn:
            return await search(
                conn,
                repo_id,
                query,
                k=k,
                mode="hybrid+rerank",
                include_tests=include_tests,
            )
    finally:
        await close_pool(pool)
