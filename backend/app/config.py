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
    ANTHROPIC_API_KEY: str | None = None
    AGENT_MODEL: str | None = None
    EMBEDDING_MODEL: str = "BAAI/bge-small-en-v1.5"
    RERANKER_MODEL: str = "BAAI/bge-reranker-v2-m3"

    # Hugging Face Hub token — optional; raises rate limits and speeds model
    # downloads. Read here so nothing else touches the environment (rule 12).
    HF_TOKEN: str | None = None


@lru_cache
def get_settings() -> Settings:
    """Return a cached `Settings` instance."""
    return Settings()  # type: ignore[call-arg]  # values sourced from env/.env
