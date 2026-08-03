"""The single place a chat model is constructed (SPEC §7.2).

``AGENT_MODEL``'s prefix selects the provider — nothing else in the codebase
imports a provider package, so switching providers is a config change and the
rest of the agent stays provider-agnostic (`.bind_tools` works on whatever
this returns).

    mistral*  -> ChatMistralAI            (MISTRAL_API_KEY)     tuning + primary measurement
    gemini*   -> ChatGoogleGenerativeAI   (GOOGLE_API_KEY)      smoke tests only
    claude*   -> ChatAnthropic            (ANTHROPIC_API_KEY)
    vertex:*  -> ChatVertexAI             (GOOGLE_APPLICATION_CREDENTIALS + GCP_PROJECT)

**This list is closed.** Four providers is already more than the project needs;
another one is a config problem masquerading as a code problem.

**Role split** (DECISIONS 2026-07-26 "Provider roles", superseding the earlier
provider-policy entry): Mistral's token-metered free tier carries tuning and
primary measurement; the AI Studio key is demoted to smoke tests after its real
limit turned out to be 20 requests/day/model — two agent runs; Vertex credits
run confirmation measurement and the strong-model diagnostic, always, because
credits expire whether or not they are spent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Imported for typing only: this module pulls in `transformers` and
    # therefore torch — 185 MB measured, more than the embedding model it
    # would sit beside. Deferring it is what keeps an API replica off torch
    # entirely (SPEC §16.1).
    from langchain_core.language_models.chat_models import BaseChatModel

import logging
import os
from typing import Any

from pydantic import SecretStr

from app.agent import langchain_merge_fix
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
    if model.startswith("mistral"):
        return "mistral"
    raise AgentError(
        f"AGENT_MODEL={model!r} has no known provider prefix "
        "(expected 'mistral…', 'gemini…', 'claude…', or 'vertex:…')"
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

    **Temperature defaults to 0 on every provider.** Measurement runs must be
    comparable across providers, and provider defaults are not: Gemini/Vertex
    default to 1.0 while Mistral was explicitly pinned to 0. The M3 runs before
    2026-07-26 therefore compared a temperature-0 model against a
    temperature-1.0 one. Pinning it here makes that impossible to repeat by
    omission. (Zero does not buy determinism — Mistral varies ±2 questions at
    temperature 0 — it only removes one avoidable source of spread.)
    """
    if temperature is None:
        temperature = 0.0
    # Every chat model is built here, so this is the one place that runs before
    # any inference on any provider. See the module docstring for the upstream
    # bug: an answer containing the word "index" crashes chunk assembly, and
    # this tool is a codebase *indexer*.
    langchain_merge_fix.apply()
    settings = get_settings()
    name = model or settings.AGENT_MODEL
    if not name:
        raise AgentError("AGENT_MODEL is not set; see backend/.env.example")

    provider = provider_for(name)
    logger.info("building chat model: %s (provider=%s)", name, provider)

    # Every provider client takes `timeout`, and every one of them defaults it
    # to something unhelpful — Anthropic to no limit at all. Without it, a
    # provider that stops responding mid-request holds an SSE stream, its
    # concurrency slot, and the caller's browser tab open indefinitely: the §7.2
    # tool cap bounds how many calls a run makes, never how long one may take.
    # Passed explicitly per provider below because Mistral's field is an `int`
    # and the rest are floats. A caller can still override through **kwargs.
    timeout_s = float(settings.AGENT_REQUEST_TIMEOUT_S)
    kwargs.setdefault("timeout", timeout_s)

    if provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        if not settings.GOOGLE_API_KEY:
            raise AgentError("GOOGLE_API_KEY is required for a 'gemini' AGENT_MODEL")
        return ChatGoogleGenerativeAI(
            model=name,
            google_api_key=settings.GOOGLE_API_KEY,
            max_output_tokens=max_tokens,
            max_retries=DEFAULT_MAX_RETRIES,
            temperature=temperature,
            **kwargs,
        )

    if provider == "mistral":
        from langchain_mistralai import ChatMistralAI

        if not settings.MISTRAL_API_KEY:
            raise AgentError(
                "MISTRAL_API_KEY is required for a 'mistral' AGENT_MODEL. "
                "Get one at console.mistral.ai (free tier needs phone "
                "verification), then add MISTRAL_API_KEY=... to backend/.env"
            )
        # Mistral declares `timeout: int`, so hand it one.
        kwargs["timeout"] = int(kwargs["timeout"])
        return ChatMistralAI(
            model_name=name,
            api_key=SecretStr(settings.MISTRAL_API_KEY),
            max_tokens=max_tokens,
            max_retries=DEFAULT_MAX_RETRIES,
            temperature=0.0 if temperature is None else temperature,
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
    # google-auth reads the credentials path from os.environ, but
    # pydantic-settings loads .env into Settings and never exports it — so a
    # value living only in .env raises DefaultCredentialsError here even
    # though it is configured. Bridge it, without clobbering a real env var.
    if settings.GOOGLE_APPLICATION_CREDENTIALS and not os.environ.get(
        "GOOGLE_APPLICATION_CREDENTIALS"
    ):
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = (
            settings.GOOGLE_APPLICATION_CREDENTIALS
        )
    return ChatVertexAI(
        model_name=name.split(":", 1)[1],
        project=settings.GCP_PROJECT,
        location=settings.GCP_LOCATION,
        max_output_tokens=max_tokens,
        max_retries=DEFAULT_MAX_RETRIES,
        temperature=temperature,
        **kwargs,
    )
