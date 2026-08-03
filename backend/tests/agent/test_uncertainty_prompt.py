"""§25 — the confidence marker's half of the contract that lives in the prompt.

The parser is on the client (it renders the marker), so what is testable here is
that the instruction is present, exact, and *calibrated*. The calibration is the
whole feature: FEATURE-IDEAS 5.4 names over-hedging as the risk, and a marker on
every answer is worth exactly as much as a marker on none.
"""

from __future__ import annotations

from app.agent.prompts import FORCED_ANSWER, UNCERTAINTY, system_prompt

# The prompt is hard-wrapped, so any phrase long enough to be worth asserting on
# will straddle a newline. Normalising is the difference between a test that
# checks the instruction and one that checks the line width.
FLAT = " ".join(UNCERTAINTY.split())


def test_the_marker_format_is_stated_exactly() -> None:
    assert "[uncertain:" in UNCERTAINTY


def test_the_prompt_carries_the_rule() -> None:
    prompt = system_prompt("owner/repo", 12, ["pkg", "tests"])
    assert "[uncertain:" in prompt


def test_it_names_when_to_emit_and_when_not_to() -> None:
    """Both halves, because only the second one prevents hedging everything."""
    assert "ONLY when" in FLAT
    assert "Do NOT emit it otherwise" in FLAT


def test_it_argues_the_cost_of_over_hedging_rather_than_just_forbidding_it() -> None:
    """A rule with a reason survives paraphrase; a bare prohibition does not."""
    assert "teaches the reader to ignore the marker" in FLAT
    assert "Hedging every answer is the same as hedging none" in FLAT


def test_it_shows_a_bad_example_not_only_a_good_one() -> None:
    """The failure mode is a generic disclaimer, so the prompt names one."""
    assert "INCORRECT:" in FLAT
    assert "I am an AI" in FLAT


def test_the_forced_answer_points_at_the_marker() -> None:
    """Running out of tool calls is the clearest legitimate use of it."""
    assert "[uncertain:" in FORCED_ANSWER


def test_the_marker_is_capped_at_one_per_answer() -> None:
    assert "at most once" in FLAT
