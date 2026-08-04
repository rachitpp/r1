"""Provider selection from ``AGENT_MODEL`` (SPEC §7.2).

The switch that makes the agent provider-agnostic had no tests, which meant the
closed four-provider list and the "temperature is pinned to 0" rule — a rule
added *because* a measurement run once compared temperature 0 against 1.0 — were
both unenforced. Nothing here performs inference.
"""

from __future__ import annotations

import pytest

from app.agent.model import build_chat_model, provider_for
from app.config import get_settings
from app.exceptions import AgentError


@pytest.mark.parametrize(
    ("model", "provider"),
    [
        ("mistral-medium-latest", "mistral"),
        ("mistral-small", "mistral"),
        ("gemini-2.0-flash", "gemini"),
        ("gemini-1.5-pro", "gemini"),
        ("claude-sonnet-4", "claude"),
        ("vertex:gemini-2.0-flash", "vertex"),
    ],
)
def test_the_prefix_selects_the_provider(model: str, provider: str) -> None:
    assert provider_for(model) == provider


@pytest.mark.parametrize(
    "model",
    [
        "gpt-4o",
        "llama-3",
        "",
        "vertex",  # the colon is what makes it a vertex model
        "Gemini-2.0",  # prefix match is case-sensitive on purpose
    ],
)
def test_an_unknown_prefix_is_refused_by_name(model: str) -> None:
    """The list is closed (§7.2), so an unrecognised model must fail loudly."""
    with pytest.raises(AgentError) as caught:
        provider_for(model)
    assert repr(model) in str(caught.value)


def test_the_error_names_every_accepted_prefix() -> None:
    """A misconfiguration should be fixable from the message alone."""
    with pytest.raises(AgentError) as caught:
        provider_for("gpt-4o")
    message = str(caught.value)
    for prefix in ("mistral", "gemini", "claude", "vertex"):
        assert prefix in message


def test_an_unset_agent_model_is_refused_before_any_provider_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "AGENT_MODEL", "")

    with pytest.raises(AgentError, match="AGENT_MODEL is not set"):
        build_chat_model()


def test_build_refuses_an_unknown_prefix_too(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "AGENT_MODEL", "gpt-4o")

    with pytest.raises(AgentError):
        build_chat_model()


@pytest.mark.parametrize(
    ("model", "setting", "expected"),
    [
        ("mistral-medium-latest", "MISTRAL_API_KEY", "MISTRAL_API_KEY"),
        ("gemini-2.0-flash", "GOOGLE_API_KEY", "GOOGLE_API_KEY"),
        ("claude-sonnet-4", "ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"),
        ("vertex:gemini-2.0-flash", "GCP_PROJECT", "GCP_PROJECT"),
    ],
)
def test_a_missing_credential_names_the_env_var(
    monkeypatch: pytest.MonkeyPatch, model: str, setting: str, expected: str
) -> None:
    """Each provider's missing-key error must say which variable to set."""
    monkeypatch.setattr(get_settings(), setting, None)

    with pytest.raises(AgentError) as caught:
        build_chat_model(model=model)
    assert expected in str(caught.value)


def test_temperature_is_pinned_to_zero_rather_than_left_to_the_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider defaults disagree (Gemini 1.0, Mistral 0), so runs would not be
    comparable across providers. Omitting `temperature` must still mean 0."""
    settings = get_settings()
    monkeypatch.setattr(settings, "MISTRAL_API_KEY", "not-a-real-key")

    model = build_chat_model(model="mistral-medium-latest")

    assert model.temperature == 0.0


def test_an_explicit_temperature_still_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "MISTRAL_API_KEY", "not-a-real-key")

    model = build_chat_model(model="mistral-medium-latest", temperature=0.7)

    assert model.temperature == 0.7


def test_the_request_timeout_is_passed_through_to_the_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Anthropic defaults to no limit at all; without this a stalled provider
    holds an SSE stream and its concurrency slot open indefinitely.

    Checked on Mistral, the default provider — it is also the one whose field is
    an ``int`` rather than a float, so it exercises the narrowing in the builder.
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "MISTRAL_API_KEY", "not-a-real-key")

    model = build_chat_model(model="mistral-medium-latest")

    assert model.timeout == int(settings.AGENT_REQUEST_TIMEOUT_S)
