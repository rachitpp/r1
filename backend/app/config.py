"""Application configuration and project-wide constants.

All environment access lives here (CLAUDE.md rule 12). The `Settings` class
loads `backend/.env` via pydantic-settings. The constants block below is the
single source of truth for SPEC §12 values — names match the SPEC exactly so
code and spec stay greppable against each other.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

# ---------------------------------------------------------------------------
# Constants — SPEC §12 (single source of truth). Do not scatter these values.
# ---------------------------------------------------------------------------

IGNORE_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        "dist",
        "build",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
    }
)
TEST_DIR_SEGMENTS: frozenset[str] = frozenset({"tests", "test", "testing"})
TEST_FILE_NAMES: frozenset[str] = frozenset({"conftest.py"})
MAX_FILE_BYTES: int = 500_000
MAX_FILES: int = 10_000
CHUNK_TOKEN_MAX: int = 480
VEC_K: int = 40
FTS_K: int = 40
RRF_K: int = 60
SEARCH_K: int = 10
RERANK_PASSAGE_TOKENS: int = 512
READ_MAX_LINES: int = 400
EXPAND_MAX_DEPTH: int = 2
EXPAND_TOKEN_BUDGET: int = 6_000
AGENT_TOOL_CAP: int = 8
CALLED_BY_MAX: int = 8  # §7.4 called-by block cap, then "+N more"
JEDI_FILE_TIMEOUT_S: int = 10
ZOMBIE_AFTER_S: int = 1_200
PROGRESS_EVERY_N: int = 25


class Settings(BaseSettings):
    """Environment-backed settings, loaded from ``backend/.env``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Required infrastructure.
    DATABASE_URL: str
    REDIS_URL: str

    # Optional / defaulted so Phase 0 runs without model keys.
    #
    # AGENT_MODEL is provider-configurable (SPEC §7.2, DECISIONS 2026-07-26):
    # the prefix selects the client built by app/agent/model.py —
    #   gemini*  -> ChatGoogleGenerativeAI  (GOOGLE_API_KEY)
    #   claude*  -> ChatAnthropic           (ANTHROPIC_API_KEY)
    #   vertex:* -> ChatVertexAI            (GOOGLE_APPLICATION_CREDENTIALS + GCP_PROJECT)
    ANTHROPIC_API_KEY: str | None = None
    AGENT_MODEL: str | None = None
    GOOGLE_API_KEY: str | None = None

    # Mistral — tuning and primary measurement. The AI Studio free tier's real
    # limit is 20 requests/day/model, which an agent loop exhausts in two runs;
    # Mistral's free tier is token-metered, which is the constraint that
    # actually binds here (DECISIONS 2026-07-26, provider roles).
    MISTRAL_API_KEY: str | None = None

    # Vertex — measurement runs and the strong-model cross-check only; default
    # tuning traffic never routes here.
    #
    # GOOGLE_APPLICATION_CREDENTIALS needs a field even though google-auth
    # reads it from os.environ: pydantic-settings loads .env into *this object*
    # and never exports it, so a value that lives only in .env is invisible to
    # google-auth. The model factory bridges the two (see app/agent/model.py).
    GCP_PROJECT: str | None = None
    GCP_LOCATION: str = "us-central1"
    GOOGLE_APPLICATION_CREDENTIALS: str | None = None
    EMBEDDING_MODEL: str = "BAAI/bge-small-en-v1.5"
    RERANKER_MODEL: str = "BAAI/bge-reranker-v2-m3"

    # Hugging Face Hub token — optional; raises rate limits and speeds model
    # downloads. Read here so nothing else touches the environment (rule 12).
    HF_TOKEN: str | None = None

    # Browser origin allowed by the CORS middleware (SPEC §8). Set before the
    # frontend exists so Phase 5 does not open on a preflight failure.
    FRONTEND_ORIGIN: str = "http://localhost:3000"

    # Cross-encoder rerank — OFF by default (SPEC §5.3, DECISIONS 2026-07-26).
    # Measured worse-or-equal than plain fusion at every k and at MRR, in both
    # corpus conditions, for 2.4 GB of resident model. Kept wired and lazily
    # loaded so the ablation stays permanently measurable
    # (`scripts/eval.py --mode hybrid+rerank`).
    RERANK_ENABLED: bool = False


@lru_cache
def get_settings() -> Settings:
    """Return a cached `Settings` instance."""
    return Settings()  # type: ignore[call-arg]  # values sourced from env/.env
