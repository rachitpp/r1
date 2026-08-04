"""The §30.4 prose exclusion, at the SQL-fragment level.

These assert on the generated predicates rather than on rows, deliberately: the
claim §30.7 makes is that the *default candidate pool does not change*, and the
way that claim fails is a leg forgetting the filter — which is visible in the
fragment and invisible in a passing row count.
"""

from __future__ import annotations

import pytest

from app.retrieval.hybrid import _pool_filter, _prose_filter, _test_filter


def test_default_excludes_both_tests_and_prose() -> None:
    assert _pool_filter(False, "exclude") == " AND NOT is_test AND NOT is_prose"


def test_the_two_conditions_are_independent() -> None:
    assert _pool_filter(True, "exclude") == " AND NOT is_prose"
    assert _pool_filter(False, "include") == " AND NOT is_test"
    assert _pool_filter(True, "include") == ""


def test_only_restricts_rather_than_widening() -> None:
    """`search_docs` asks for prose *and nothing else* (§30.5)."""
    assert _prose_filter("only") == " AND is_prose"
    # Still implementation-scoped on the test axis: a doc-shaped question should
    # not surface a test fixture's prose either.
    assert _pool_filter(False, "only") == " AND NOT is_test AND is_prose"


@pytest.mark.parametrize("alias", ["", "c."])
def test_the_alias_reaches_both_predicates(alias: str) -> None:
    """The FTS leg joins, so its predicates need a table alias — both of them."""
    fragment = _pool_filter(False, "exclude", alias=alias)
    assert f"NOT {alias}is_test" in fragment
    assert f"NOT {alias}is_prose" in fragment


def test_the_test_filter_is_unchanged_by_30() -> None:
    """§5.4 behaviour is untouched; §30 adds a second condition beside it."""
    assert _test_filter(False) == " AND NOT is_test"
    assert _test_filter(True) == ""
