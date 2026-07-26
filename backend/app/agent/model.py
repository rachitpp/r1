"""The single place a chat model is constructed (SPEC §7.2).

``AGENT_MODEL``'s prefix selects the provider — nothing else in the codebase
imports a provider package, so switching providers is a config change and the
rest of the agent stays provider-agnostic (`.bind_tools` works on whatever
this returns).

    gemini*   -> ChatGoogleGenerativeAI   (GOOGLE_API_KEY)      free tier, default
    claude*   -> ChatAnthropic            (ANTHROPIC_API_KEY)
    vertex:*  -> ChatVertexAI             (GOOGLE_APPLICATION_CREDENTIALS + GCP_PROJECT)

**Usage policy** (DECISIONS 2026-07-26): tuning runs on the free AI Studio key;
Vertex is for measurement runs and the strong-model cross-check only. Default
traffic never routes through Vertex — trial credits are a measurement budget,
not a tuning budget.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import SecretStr

from app.config import get_settings
from app.exceptions import AgentError

logger = logging.getLogger(__name__)

# Free-tier quotas are tight (~10-15 RPM) and 429s are routine rather than
# exceptional, so retries are configured on the client itself.
DEFAULT_MAX_RETRIES = 5


def provider_for(model: str) -> str:
    """Provider name for ``model``'s prefix; raises on an unknown shape."""
    if model.startswith("vertex:"):
        return "vertex"
    if model.startswith("gemini"):
        return "gemini"
    if model.startswith("claude"):
        return "claude"
    raise AgentError(
        f"AGENT_MODEL={model!r} has no known provider prefix "
        "(expected 'gemini…', 'claude…', or 'vertex:…')"
    )


def build_chat_model(
    *,
    model: str | None = None,
    max_tokens: int = 4096,
    temperature: float | None = None,
    **kwargs: Any,
) -> BaseChatModel:
    """Construct the chat client for ``model`` (default: ``AGENT_MODEL``).

    Provider packages are imported lazily so a run on one provider never pays
    the import cost — or the failure — of the others.
    """
    settings = get_settings()
    name = model or settings.AGENT_MODEL
    if not name:
        raise AgentError("AGENT_MODEL is not set; see backend/.env.example")

    provider = provider_for(name)
    logger.info("building chat model: %s (provider=%s)", name, provider)

    if provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        if not settings.GOOGLE_API_KEY:
            raise AgentError("GOOGLE_API_KEY is required for a 'gemini' AGENT_MODEL")
        return ChatGoogleGenerativeAI(
            model=name,
            google_api_key=settings.GOOGLE_API_KEY,
            max_output_tokens=max_tokens,
            max_retries=DEFAULT_MAX_RETRIES,
            **({"temperature": temperature} if temperature is not None else {}),
            **kwargs,
        )

    if provider == "claude":
        from langchain_anthropic import ChatAnthropic

        if not settings.ANTHROPIC_API_KEY:
            raise AgentError("ANTHROPIC_API_KEY is required for a 'claude' AGENT_MODEL")
        return ChatAnthropic(
            model_name=name,
            api_key=SecretStr(settings.ANTHROPIC_API_KEY),
            max_tokens_to_sample=max_tokens,
            max_retries=DEFAULT_MAX_RETRIES,
            timeout=None,
            stop=None,
            **kwargs,
        )

    # vertex — measurement runs and the cross-check only.
    from langchain_google_vertexai import ChatVertexAI

    if not settings.GCP_PROJECT:
        raise AgentError(
            "GCP_PROJECT and GOOGLE_APPLICATION_CREDENTIALS are required for a "
            "'vertex:' AGENT_MODEL"
        )
    return ChatVertexAI(
        model_name=name.split(":", 1)[1],
        project=settings.GCP_PROJECT,
        location=settings.GCP_LOCATION,
        max_output_tokens=max_tokens,
        max_retries=DEFAULT_MAX_RETRIES,
        **kwargs,
    )
