"""§23.2 — prior turns as agent context.

`prior_turns_as_messages` is pure, so this is the cheap place to pin the two
properties that actually bound cost: what shape the history takes, and that a
long answer cannot quietly become the largest part of every later prompt.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage

from app.agent.graph import prior_turns_as_messages
from app.config import CONVERSATION_ANSWER_CHARS

TURNS = [
    {"question": "how does auth work?", "answer": "It verifies a token."},
    {"question": "and where is that called?", "answer": "In the middleware."},
]


def test_turns_become_alternating_human_and_ai_messages() -> None:
    msgs = prior_turns_as_messages(TURNS)
    assert [type(m) for m in msgs] == [HumanMessage, AIMessage, HumanMessage, AIMessage]
    assert msgs[0].content == "how does auth work?"
    assert msgs[1].content == "It verifies a token."


def test_order_is_oldest_first() -> None:
    """A follow-up reads back through the exchange in the order it happened."""
    msgs = prior_turns_as_messages(TURNS)
    assert msgs[2].content == "and where is that called?"


def test_no_history_is_no_messages() -> None:
    """The single-shot run must be byte-identical to what it was before §23."""
    assert prior_turns_as_messages([]) == []


def test_a_long_answer_is_truncated() -> None:
    """Otherwise the window becomes the largest part of every later prompt."""
    long = [{"question": "q", "answer": "x" * (CONVERSATION_ANSWER_CHARS * 3)}]
    msgs = prior_turns_as_messages(long)
    assert len(str(msgs[1].content)) < CONVERSATION_ANSWER_CHARS + 100


def test_truncation_is_marked_not_silent() -> None:
    """A model that cannot see the cut treats half a sentence as the whole claim."""
    long = [{"question": "q", "answer": "y" * (CONVERSATION_ANSWER_CHARS * 2)}]
    assert "truncated" in str(prior_turns_as_messages(long)[1].content)


def test_a_short_answer_is_left_exactly_alone() -> None:
    msgs = prior_turns_as_messages([{"question": "q", "answer": "brief."}])
    assert msgs[1].content == "brief."


def test_questions_are_never_truncated() -> None:
    """The question is what a follow-up refers back to, and it is short anyway."""
    q = "why " * 400
    msgs = prior_turns_as_messages([{"question": q, "answer": "a"}])
    assert msgs[0].content == q
