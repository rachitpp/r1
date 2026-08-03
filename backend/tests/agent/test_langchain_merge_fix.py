"""The langchain-core `merge_lists` guard.

Reported from a live run: an answer streamed in full and was then followed by
"Something went wrong", losing its citations. The cause is upstream and its
trigger is absurd — the crash only happens when the answer text contains the
substring "index", because an unguarded ``"index" in e_left`` on a ``str``
is a substring test rather than a key test.

This project is a codebase *indexer*, so that substring is domain vocabulary
rather than an edge case.
"""

from __future__ import annotations

from langchain_core.messages import AIMessageChunk

from app.agent.langchain_merge_fix import apply


def test_upstream_crashes_on_mixed_content_containing_the_word_index() -> None:
    """Pins the bug itself, so this test starts failing when it is fixed.

    That is the intended signal: when upstream guards the comparison, this
    assertion fails, and the patch beside it can be deleted rather than
    carried forever by nobody quite daring to remove it.
    """
    from langchain_core.utils import _merge

    # The unpatched original, reached regardless of import order.
    original = getattr(_merge.merge_lists, "__wrapped__", _merge.merge_lists)

    left = ["the symbol index is built at ingest"]
    right = [{"index": 0, "type": "text", "text": " and stored"}]
    try:
        original(left, right)
    except TypeError as exc:
        assert "string indices" in str(exc)
    else:  # pragma: no cover — reached only once upstream fixes it
        raise AssertionError(
            "langchain-core no longer crashes here; delete "
            "app/agent/langchain_merge_fix.py and this test"
        )


def test_the_same_text_without_the_substring_never_crashed() -> None:
    """Why it looked intermittent: it depends on the words in the answer."""
    from langchain_core.utils import _merge

    original = getattr(_merge.merge_lists, "__wrapped__", _merge.merge_lists)
    merged = original(
        ["the symbol map is built at ingest"],
        [{"index": 0, "type": "text", "text": " and stored"}],
    )
    assert merged is not None


def test_the_patch_survives_the_crashing_shape() -> None:
    apply()
    from langchain_core.utils import _merge

    merged = _merge.merge_lists(
        ["the symbol index is built at ingest"],
        [{"index": 0, "type": "text", "text": " and stored"}],
    )
    assert merged is not None
    # Content is preserved, not dropped — the string became a text block.
    flat = str(merged)
    assert "the symbol index is built at ingest" in flat
    assert "and stored" in flat


def test_message_chunks_containing_index_can_be_added() -> None:
    """The real path: `AIMessageChunk.__add__`, which is what streaming uses."""
    apply()
    left = AIMessageChunk(content=["indexed at ingest"])
    right = AIMessageChunk(content=[{"index": 0, "type": "text", "text": " ok"}])
    combined = left + right
    assert combined is not None


def test_apply_is_idempotent() -> None:
    apply()
    assert apply() == 0, "a second call must not stack another wrapper"


def test_the_patch_does_not_change_ordinary_merges() -> None:
    """Inert unless the original raises: normal content merges byte-identically."""
    apply()
    from langchain_core.utils import _merge

    original = getattr(_merge.merge_lists, "__wrapped__", _merge.merge_lists)
    left = [{"index": 0, "type": "text", "text": "a"}]
    right = [{"index": 0, "type": "text", "text": "b"}]
    assert _merge.merge_lists(left, right) == original(left, right)
