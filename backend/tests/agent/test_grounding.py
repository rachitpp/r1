"""Citation grounding (§27, FEATURE-IDEAS 5.1).

The cases that matter are the two failure modes of a lexical check: calling a
good citation bad (noise nobody will trust twice), and calling everything good
(a check that always passes is not a check). `unchecked` is asserted as hard as
the other two — it is what keeps the method from manufacturing warnings out of
its own blind spot.
"""

from __future__ import annotations

from app.agent.citations import Citation
from app.agent.grounding import (
    claim_for,
    claim_identifiers,
    ground_answer,
)

# The real blinker answer this project produced, trimmed. Using a genuine
# answer rather than an invented one because the shape — backticked
# identifiers, a bullet per claim, a citation terminating each — is the thing
# the extractor is written against.
BLINKER_ANSWER = """To connect a receiver to a signal, blinker's `Signal.connect` method records
the receiver in two lookup dictionaries.

- A unique `receiver_id` and `sender_id` are created via `make_id` [src/blinker/base.py:107-108].
- The method then adds the `receiver_id` to `self._by_sender` [src/blinker/base.py:117-118].
- This is where the request begins [src/blinker/base.py:1-2].
"""

BLINKER_SOURCE = "\n".join(
    [
        "line1",  # 1
        "line2",  # 2
    ]
    + [f"filler{i}" for i in range(3, 107)]
    + [
        "        receiver_id = make_id(receiver)",  # 107
        "        sender_id = ANY_ID if sender is ANY else make_id(sender)",  # 108
    ]
    + [f"filler{i}" for i in range(109, 117)]
    + [
        "        self._by_sender[sender_id].add(receiver_id)",  # 117
        "        self._by_receiver[receiver_id].add(sender_id)",  # 118
    ]
)


def cite(path: str, start: int, end: int) -> Citation:
    return {"file_path": path, "start_line": start, "end_line": end}


# --- claim extraction ------------------------------------------------------


def test_claim_stops_at_the_bullet_boundary() -> None:
    """Taking the paragraph would drag in a neighbour's identifiers."""
    end = BLINKER_ANSWER.index("[src/blinker/base.py:117-118]") + len(
        "[src/blinker/base.py:117-118]"
    )
    claim = claim_for(BLINKER_ANSWER, end)
    assert "_by_sender" in claim
    # The previous bullet's identifier must not leak into this claim.
    assert "make_id" not in claim


def test_claim_drops_earlier_citation_markers() -> None:
    answer = "First [a/b.py:1-2]. Then `thing` matters [a/b.py:5-6]."
    end = answer.rindex("]") + 1
    claim = claim_for(answer, end)
    assert "a/b.py" not in claim
    assert "thing" in claim


# --- identifier extraction -------------------------------------------------


def test_backticked_spans_are_taken_whole_and_in_parts() -> None:
    found = claim_identifiers("blinker's `Signal.connect` method records it")
    assert "Signal.connect" in found
    assert "Signal" in found
    assert "connect" in found


def test_prose_words_outside_backticks_are_not_identifiers() -> None:
    """Otherwise ordinary English matches any code containing the same word."""
    found = claim_identifiers("The method then adds the receiver to the mapping")
    assert found == []


def test_camel_and_underscore_shapes_count_outside_backticks() -> None:
    found = claim_identifiers("It builds a WSGIEnvironment from start_response")
    assert "WSGIEnvironment" in found
    assert "start_response" in found


def test_stopwords_are_excluded_outside_backticks() -> None:
    """A check that matches on `the` and `method` always passes."""
    assert claim_identifiers("the method is called on the class") == []


def test_a_backticked_identifier_survives_the_stopword_list() -> None:
    """Found on a real answer: blinker's sentinel is literally `ANY`.

    The backtick is the model marking a token as code; overriding that with an
    English word list discards real identifiers that happen to spell a common
    word, and the claim scored `unchecked` when it was perfectly checkable.
    """
    assert "ANY" in claim_identifiers("If the sender is not `ANY` it stores a ref")
    assert "set" in claim_identifiers("it calls `set` on the mapping")


# --- verdicts --------------------------------------------------------------


def test_citation_whose_lines_contain_the_claimed_identifier_is_supported() -> None:
    grounded = ground_answer(
        BLINKER_ANSWER,
        [cite("src/blinker/base.py", 107, 108)],
        {"src/blinker/base.py": BLINKER_SOURCE},
    )
    assert grounded[0]["verdict"] == "supported"
    assert "make_id" in grounded[0]["matched"]


def test_each_citation_is_scored_against_its_own_claim() -> None:
    grounded = ground_answer(
        BLINKER_ANSWER,
        [
            cite("src/blinker/base.py", 107, 108),
            cite("src/blinker/base.py", 117, 118),
        ],
        {"src/blinker/base.py": BLINKER_SOURCE},
    )
    assert [g["verdict"] for g in grounded] == ["supported", "supported"]
    assert "_by_sender" in grounded[1]["matched"]


def test_citation_pointing_at_unrelated_lines_is_unsupported() -> None:
    """The failure this feature exists for: present, valid, and wrong."""
    answer = "The receiver is stored via `make_id` [src/blinker/base.py:1-2]."
    grounded = ground_answer(
        answer,
        [cite("src/blinker/base.py", 1, 2)],
        {"src/blinker/base.py": BLINKER_SOURCE},
    )
    assert grounded[0]["verdict"] == "unsupported"
    assert "make_id" in grounded[0]["missing"]


def test_claim_naming_no_identifiers_is_unchecked_not_unsupported() -> None:
    """The method's blind spot must not be reported as a finding."""
    grounded = ground_answer(
        BLINKER_ANSWER,
        [cite("src/blinker/base.py", 1, 2)],
        {"src/blinker/base.py": BLINKER_SOURCE},
    )
    assert grounded[0]["verdict"] == "unchecked"
    assert grounded[0]["matched"] == []


def test_unreadable_file_is_unchecked_not_unsupported() -> None:
    """A gap in this check is not evidence against the answer."""
    answer = "It uses `make_id` [other/file.py:1-2]."
    grounded = ground_answer(answer, [cite("other/file.py", 1, 2)], {})
    assert grounded[0]["verdict"] == "unchecked"


def test_no_citations_grounds_to_nothing() -> None:
    assert ground_answer("A plain answer.", [], {}) == []


def test_a_hard_wrapped_citation_keeps_its_claim() -> None:
    """Found on a live answer: the model wraps and puts the citation on its
    own continuation line, so the nearest boundary yields only whitespace.

    Every wrapped citation scored `unchecked` — silently, because that is also
    what a genuinely uncheckable claim looks like. All four citations in the
    observed answer were affected.
    """
    answer = (
        "- `build_chunks` reads every PDF and splits prose into chunks\n"
        "  [app.py:404-449]\n"
    )
    end = answer.index("]") + 1
    claim = claim_for(answer, end)
    assert "build_chunks" in claim
    assert "build_chunks" in claim_identifiers(claim)


def test_a_wrapped_citation_can_now_be_supported() -> None:
    answer = (
        "- `make_id` assigns the receiver identity\n"
        "  [src/blinker/base.py:107-108]\n"
    )
    grounded = ground_answer(
        answer,
        [cite("src/blinker/base.py", 107, 108)],
        {"src/blinker/base.py": BLINKER_SOURCE},
    )
    assert grounded[0]["verdict"] == "supported"


def test_walking_back_stops_before_stealing_the_previous_claim() -> None:
    """Three hops is the budget; it must not reach into the bullet above."""
    answer = (
        "- `alpha_helper` does one thing [a.py:1-2]\n"
        "\n"
        "\n"
        "\n"
        "  [a.py:5-6]\n"
    )
    end = answer.rindex("]") + 1
    assert "alpha_helper" not in claim_for(answer, end)
