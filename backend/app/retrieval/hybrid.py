"""Hybrid retrieval: RRF fusion + exact-symbol injection + rerank (SPEC §5).

``hybrid_search(snapshot_id, query, k=SEARCH_K)`` is the single public entry point
for feature code (CLAUDE.md hard rule 2). The default pipeline is vector ⊕ FTS
fused with RRF over implementation chunks; the cross-encoder rerank is **off by
default** (``RERANK_ENABLED``, SPEC §5.3) because it measured worse-or-equal
than plain fusion at every k and at MRR.

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
from app.db.pool import ConnSource, acquire, close_pool, create_pool
from app.ingest.embedder import encode_async, rerank_async

Mode = Literal["vector", "fts", "hybrid", "hybrid+rerank"]
MODES: tuple[Mode, ...] = ("vector", "fts", "hybrid", "hybrid+rerank")

# What §30 prose/config chunks do to the candidate pool.
#
#   exclude — the default and the shipped behaviour (§30.4).
#   include — blend prose into the default pool. The counterfactual §30.4 keeps
#             measurable rather than merely asserted (`eval.py --include-prose`).
#   only    — restrict the pool to prose, which is `search_docs` (§30.5).
#
# A tri-state rather than the SPEC sketch's `include_prose: bool`, because
# `search_docs` needs "only" and a bool cannot say it. The alternative — a second
# boolean — has four spellings for three meanings, one of them contradictory.
# One entry point still, so CLAUDE.md rule 2 holds.
ProsePolicy = Literal["exclude", "include", "only"]

# Fusion returns at most this many candidates (SPEC §5.1 outer LIMIT / §5.3
# "fusion top-40"); injection may add up to INJECT_LIMIT more before rerank.
FUSION_LIMIT = 40
INJECT_LIMIT = 10
PREVIEW_CHARS = 200

# Retrieval targets implementation by default (SPEC §5.4, §30.4). Test and prose
# chunks stay in the corpus but are filtered out of every candidate-producing
# query unless explicitly included. Appended to a WHERE clause that already has a
# predicate; carries no bind parameter.
_NO_TESTS = " AND NOT is_test"
_NO_PROSE = " AND NOT is_prose"


def _test_filter(include_tests: bool, *, alias: str = "") -> str:
    """SQL fragment excluding test chunks, or empty when they are included."""
    if include_tests:
        return ""
    return f" AND NOT {alias}is_test" if alias else _NO_TESTS


def _prose_filter(prose: ProsePolicy, *, alias: str = "") -> str:
    """SQL fragment applying the §30.4 prose condition.

    Mechanically identical to :func:`_test_filter`, and for a stronger version of
    the same reason. §5.4 excludes tests because they are written in user
    vocabulary while implementation is terse; a README is not merely user
    vocabulary, it is the *same prose register as the question*. Blended into the
    default pool it would outrank implementation harder than tests ever did.
    """
    if prose == "include":
        return ""
    if prose == "only":
        return f" AND {alias}is_prose"
    return f" AND NOT {alias}is_prose" if alias else _NO_PROSE


def _pool_filter(
    include_tests: bool, prose: ProsePolicy, *, alias: str = ""
) -> str:
    """Both corpus conditions, in the order the SPEC lists them."""
    return _test_filter(include_tests, alias=alias) + _prose_filter(prose, alias=alias)


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
    conn: asyncpg.Connection,
    snapshot_id: UUID,
    query: str,
    *,
    include_tests: bool = False,
    prose: ProsePolicy = "exclude",
) -> list[int]:
    """Chunk ids whose symbol matches a query identifier (SPEC §5.2).

    Match rule (Reconciliation 1): ``symbol = name`` OR ``symbol`` ends with
    ``'.' + name``, against ``chunks.symbol`` (the symbols table does not exist
    until Phase 3). The suffix test was a ``LIKE '%.'||name`` until 2026-08-02,
    when `_` — a LIKE wildcard, and the most common character in a Python
    identifier — was found matching `json.dumps` for a query of `json_dumps`.
    Test chunks are excluded unless ``include_tests`` (SPEC §5.4) — otherwise a
    query naming a symbol would inject its test alongside the implementation.

    Prose is excluded by the same §30.4 condition as the fusion legs. It would
    almost never match anyway — ``chunks.symbol`` is NULL for every prose chunk —
    but "almost never" is not the guarantee the exclusion is supposed to give.
    """
    names = extract_identifiers(query)
    if not names:
        return []
    suffixes = [f".{name}" for name in names]
    rows = await conn.fetch(
        f"""
        SELECT id FROM chunks
        WHERE snapshot_id = $1
          AND (symbol = ANY($2::text[])
               OR EXISTS (SELECT 1 FROM unnest($3::text[]) AS sfx
                           WHERE right(symbol, length(sfx)) = sfx))
          {_pool_filter(include_tests, prose)}
        ORDER BY id
        LIMIT $4
        """,
        snapshot_id,
        names,
        suffixes,
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
    snapshot_id: UUID,
    qvec: list[float],
    limit: int,
    *,
    include_tests: bool = False,
    prose: ProsePolicy = "exclude",
) -> list[tuple[int, float]]:
    """Top-``limit`` chunk ids by cosine similarity, with similarity scores."""
    rows = await conn.fetch(
        f"""
        SELECT id, 1 - (embedding <=> $1) AS score
        FROM chunks WHERE snapshot_id = $2{_pool_filter(include_tests, prose)}
        ORDER BY embedding <=> $1, id
        LIMIT $3
        """,
        qvec,
        snapshot_id,
        limit,
    )
    return [(int(r["id"]), float(r["score"])) for r in rows]


async def _fts_leg(
    conn: asyncpg.Connection,
    snapshot_id: UUID,
    query: str,
    limit: int,
    *,
    include_tests: bool = False,
    prose: ProsePolicy = "exclude",
) -> list[tuple[int, float]]:
    """Top-``limit`` chunk ids by FTS ts_rank, with rank scores."""
    rows = await conn.fetch(
        f"""
        SELECT c.id, ts_rank(c.tsv, q) AS score
        FROM chunks c, {_fts_query_sql("$1")} AS q
        WHERE c.snapshot_id = $2 AND c.tsv @@ q
              {_pool_filter(include_tests, prose, alias="c.")}
        ORDER BY score DESC, c.id
        LIMIT $3
        """,
        query,
        snapshot_id,
        limit,
    )
    return [(int(r["id"]), float(r["score"])) for r in rows]


async def _fusion(
    conn: asyncpg.Connection,
    snapshot_id: UUID,
    qvec: list[float],
    query: str,
    *,
    include_tests: bool = False,
    prose: ProsePolicy = "exclude",
) -> list[tuple[int, float]]:
    """RRF-fuse the vector and FTS legs in one SQL statement (SPEC §5.1).

    Must run inside a transaction that has set ``hnsw.ef_search`` — the HNSW
    index applies the ``snapshot_id`` filter post-scan, and the default ef_search
    starves filtered results on multi-repo databases.

    **Every ordering here carries ``id`` as a tiebreaker**, in the window and in
    the matching ``ORDER BY`` alike. Without one, tied rows come back in
    physical heap order, so any ``UPDATE``, ``VACUUM FULL``, restore, or
    re-ingest silently reshuffles results — and ties are the common case, not
    the corner: on the benchmark corpus 20/20 questions carry tied ``ts_rank``
    scores and 20/20 carry tied RRF sums, because ``1/(k+rank)`` from a single
    leg collides exactly whenever two chunks hold the same rank in the two
    legs. Threshold metrics survive that; MRR does not (DECISIONS 2026-07-29).

    Both CTEs apply the §5.4 test filter **and** the §30.4 prose filter, so
    exclusion happens *before* the per-leg LIMIT — an excluded chunk never
    consumes a top-40 slot that implementation could have taken.
    """
    pool = _pool_filter(include_tests, prose)
    rows = await conn.fetch(
        f"""
        WITH vec AS (
          SELECT id, ROW_NUMBER() OVER (ORDER BY embedding <=> $1, id) AS rnk
          FROM chunks WHERE snapshot_id = $2{pool}
          ORDER BY embedding <=> $1, id LIMIT $4
        ),
        fts AS (
          SELECT id, ROW_NUMBER() OVER (ORDER BY ts_rank(tsv, q) DESC, id) AS rnk
          FROM chunks, {_fts_query_sql("$3")} AS q
          WHERE snapshot_id = $2 AND tsv @@ q{pool}
          ORDER BY ts_rank(tsv, q) DESC, id LIMIT $5
        )
        SELECT COALESCE(v.id, f.id) AS chunk_id,
               COALESCE(1.0/($6 + v.rnk), 0) + COALESCE(1.0/($6 + f.rnk), 0) AS rrf
        FROM vec v FULL OUTER JOIN fts f ON v.id = f.id
        ORDER BY rrf DESC, chunk_id
        LIMIT $7
        """,
        qvec,
        snapshot_id,
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
    snapshot_id: UUID,
    query: str,
    *,
    k: int = SEARCH_K,
    mode: Mode = "hybrid+rerank",
    include_tests: bool = False,
    prose: ProsePolicy = "exclude",
) -> list[SearchHit]:
    """Retrieve the top-``k`` chunks for ``query`` under the given ``mode``.

    ``hybrid+rerank`` is the production pipeline; the other modes exist for the
    eval/debug diagnostics. Vector and hybrid modes embed the query; FTS does
    not — and does not load the embedder, so ``--mode fts`` runs model-free.
    Only ``hybrid+rerank`` performs symbol injection and reranking.

    ``include_tests`` (default False) is the SPEC §5.4 corpus condition: test
    chunks are excluded from every leg and from injection. Passing True restores
    the shadowed condition and exists to keep that counterfactual measurable.

    ``prose`` is the same idea for §30.4 — ``"exclude"`` is the shipped pool,
    ``"include"`` blends prose in for the counterfactual, ``"only"`` restricts to
    it and is what ``search_docs`` calls.
    """
    if mode == "fts":
        scored = await _fts_leg(
            conn, snapshot_id, query, k, include_tests=include_tests, prose=prose
        )
        rows = await _fetch_rows(conn, [i for i, _ in scored])
        return [_hit(rows[i], s) for i, s in scored if i in rows]

    # Off the event loop: this is a torch forward pass, and run inline it stalls
    # every other request in the process for its duration (see embedder module).
    qvec = (await encode_async([query]))[0]

    if mode == "vector":
        async with conn.transaction():
            await _set_ef_search(conn)
            scored = await _vector_leg(
                conn, snapshot_id, qvec, k, include_tests=include_tests, prose=prose
            )
        rows = await _fetch_rows(conn, [i for i, _ in scored])
        return [_hit(rows[i], s) for i, s in scored if i in rows]

    # hybrid / hybrid+rerank both start from RRF fusion.
    async with conn.transaction():
        await _set_ef_search(conn)
        fused = await _fusion(
            conn, snapshot_id, qvec, query, include_tests=include_tests, prose=prose
        )

    if mode == "hybrid":
        rows = await _fetch_rows(conn, [i for i, _ in fused])
        ordered = [_hit(rows[i], s) for i, s in fused if i in rows]
        return ordered[:k]

    # hybrid+rerank: fusion ∪ injection, deduped, cross-encoder reranked.
    injected = await _inject_symbol_ids(
        conn, snapshot_id, query, include_tests=include_tests, prose=prose
    )
    pool_ids: list[int] = list(dict.fromkeys([i for i, _ in fused] + injected))
    rows = await _fetch_rows(conn, pool_ids)
    pool_ids = [i for i in pool_ids if i in rows]
    if not pool_ids:
        return []
    passages = [f"{rows[i]['header']}\n{rows[i]['code']}" for i in pool_ids]
    scores = await rerank_async(query, passages)
    ranked = sorted(zip(pool_ids, scores, strict=True), key=lambda t: t[1], reverse=True)
    return [_hit(rows[i], s) for i, s in ranked[:k]]


async def hybrid_search(
    snapshot_id: UUID,
    query: str,
    k: int = SEARCH_K,
    *,
    source: ConnSource | None = None,
    include_tests: bool = False,
    prose: ProsePolicy = "exclude",
    rerank: bool | None = None,
) -> list[SearchHit]:
    """The single public retrieval entry point (SPEC §5.3, CLAUDE.md rule 2).

    Runs the default production pipeline. Feature code (agent, API) calls this;
    it never touches the single-signal legs directly.

    **Pass ``source``** — a pool or a connection — from anything that serves
    requests. Without it this function builds an entire asyncpg pool, uses one
    connection, and tears the pool down again: several TCP handshakes and
    ``init`` round-trips per search, on a code path a user is waiting on. That
    self-managed path exists for the standalone scripts (``scripts/eval.py``,
    ``scripts/debug_search.py``), which have no pool and run one query at a time.

    **Rerank is OFF by default** (``RERANK_ENABLED``, SPEC §5.3): the default
    pipeline returns RRF fusion order. The cross-encoder measured worse-or-equal
    than plain fusion at every k and at MRR, in both corpus conditions
    (DECISIONS 2026-07-26). Pass ``rerank=True``, or set ``RERANK_ENABLED``, to
    re-enable it; it stays wired and lazily loaded so the ablation remains
    measurable rather than deleted.

    Note that §5.2 symbol injection rides the rerank path — injected chunks
    carry no RRF score, so there is nothing to order them by in fusion-only
    mode. Turning rerank off therefore also turns injection off; this is the
    exact configuration measured at `hybrid` hit@10 0.95.

    Retrieval targets implementation by default: test chunks are flagged at
    ingest and filtered here (SPEC §5.4), and §30 prose/config chunks are
    excluded by ``prose="exclude"``. All of them remain in the ``files`` table,
    so ``read_file`` and ``list_directory`` still see them; prose is reached
    deliberately, through ``search_docs`` (§30.5).
    """
    settings = get_settings()
    use_rerank = settings.RERANK_ENABLED if rerank is None else rerank
    mode: Mode = "hybrid+rerank" if use_rerank else "hybrid"

    if source is not None:
        async with acquire(source) as conn:
            return await search(
                conn,
                snapshot_id,
                query,
                k=k,
                mode=mode,
                include_tests=include_tests,
                prose=prose,
            )

    pool = await create_pool(settings.DATABASE_URL, min_size=1, max_size=2)
    try:
        async with acquire(pool) as conn:
            return await search(
                conn,
                snapshot_id,
                query,
                k=k,
                mode=mode,
                include_tests=include_tests,
                prose=prose,
            )
    finally:
        await close_pool(pool)
