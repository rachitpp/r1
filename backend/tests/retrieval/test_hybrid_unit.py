"""Unit tests for the pure retrieval helpers — no DB, no model downloads."""

from __future__ import annotations

from app.config import RRF_K
from app.retrieval.hybrid import extract_identifiers, qualname_matches, rrf_fuse


def test_rrf_fuse_sums_reciprocal_ranks_across_lists() -> None:
    # id 20 appears in both lists (fts rank 1, vec rank 2) -> highest score.
    fused = rrf_fuse([[10, 20, 30], [20, 40]], k=RRF_K)
    order = [cid for cid, _ in fused]
    assert order == [20, 10, 40, 30]
    top_id, top_score = fused[0]
    assert top_id == 20
    assert top_score == 1 / (RRF_K + 1) + 1 / (RRF_K + 2)


def test_rrf_fuse_single_list_preserves_order() -> None:
    fused = rrf_fuse([[1, 2, 3]])
    assert [cid for cid, _ in fused] == [1, 2, 3]


def test_rrf_fuse_ties_break_by_id_ascending() -> None:
    # Same score (both rank 1 in their own list) -> deterministic by id.
    fused = rrf_fuse([[2], [1]])
    assert [cid for cid, _ in fused] == [1, 2]


def test_extract_identifiers_keeps_underscore_camelcase_dot() -> None:
    assert extract_identifiers("where is verify_token defined") == ["verify_token"]
    assert extract_identifiers("the DigestAuth class") == ["DigestAuth"]
    assert extract_identifiers("call httpx.get here") == ["httpx.get"]


def test_extract_identifiers_drops_plain_and_allcaps_words() -> None:
    # Plain lowercase (FTS handles these) and all-caps acronyms are dropped.
    assert extract_identifiers("where is the get function") == []
    assert extract_identifiers("URL handling logic") == []


def test_extract_identifiers_drops_sentence_initial_capitals() -> None:
    """The bug this rule fixes: a capitalised first word is not an identifier.

    Injection previously extracted ``How``/``When`` from ordinary question text
    (observed in the 2026-07-26 debug runs on q08/q14). CamelCase now requires an
    uppercase letter at a *non-initial* position plus a lowercase letter.
    """
    assert extract_identifiers("How does httpx decode a body?") == []
    assert extract_identifiers("When I pass auth, what is added?") == []
    assert extract_identifiers("Where is the timeout set?") == []
    # Trade-off of the same rule: a single-capital class name is indistinguishable
    # from a sentence-initial capital, so `Timeout`/`Response` are dropped too.
    # FTS and the vector leg still cover them; only §5.2 injection is forgone.
    assert extract_identifiers("the Timeout config") == []


def test_extract_identifiers_keeps_internal_capital_identifiers() -> None:
    assert extract_identifiers("How does BasicAuth work?") == ["BasicAuth"]
    assert extract_identifiers("the URLPattern matcher") == ["URLPattern"]
    assert extract_identifiers("What does TextDecoder do?") == ["TextDecoder"]


def test_extract_identifiers_dedupes_preserving_order() -> None:
    assert extract_identifiers("BasicAuth then BasicAuth again DigestAuth") == [
        "BasicAuth",
        "DigestAuth",
    ]


def test_qualname_matches_equal_or_dotted_suffix() -> None:
    assert qualname_matches("httpx._config.Timeout", "Timeout")
    assert qualname_matches(
        "httpx._client.BaseClient.build_request", "build_request"
    )
    assert qualname_matches("Timeout", "Timeout")


def test_qualname_matches_rejects_partial_and_none() -> None:
    assert not qualname_matches("httpx._config.Timeout", "config")
    assert not qualname_matches("mybuild_request", "build_request")
    assert not qualname_matches(None, "Timeout")
