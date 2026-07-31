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
# Phase 6 naive-chunking baseline only (SPEC §2.7). Deliberately the common
# off-the-shelf window/overlap default, fixed a priori and never tuned against
# the benchmark — a baseline picked to lose is not evidence.
NAIVE_CHUNK_CHARS: int = 1_000
NAIVE_CHUNK_OVERLAP_CHARS: int = 100
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
# §18 graph views. Caps, not page sizes: both endpoints roll the whole symbol
# graph up in one statement, and a 10_000-file repo would otherwise serialise a
# module map nobody can read. Ranked before truncation, so what survives is the
# top of the ranking rather than an arbitrary prefix.
ARCH_MAX_NODES: int = 200
ARCH_MAX_EDGES: int = 1_000
COVERAGE_MAX_LINKS: int = 500
# §19 overview. These bound the *prompt*, not a response: everything gathered
# here is pasted into one model context, so each cap is tokens somebody pays
# for. Sized to brief a reader, not to be exhaustive.
OVERVIEW_MAX_MODULES: int = 15
OVERVIEW_MAX_ENTRY_POINTS: int = 8
OVERVIEW_MAX_API_SYMBOLS: int = 25
OVERVIEW_MAX_KEY_SYMBOLS: int = 15
# Conventional names for "execution starts here" (§19.2). A convention is
# evidence, not proof — the shape signal beside it catches the rest.
ENTRY_POINT_FILENAMES: frozenset[str] = frozenset(
    {"__main__.py", "cli.py", "main.py", "app.py", "server.py", "manage.py"}
)

# ---------------------------------------------------------------------------
# Serving limits — NOT SPEC §12. These bound what one HTTP request may cost.
#
# They are constants rather than settings because they are part of the API
# contract (a client can rely on them), not a per-deployment knob. Everything
# an operator has to size against their own hardware lives on `Settings` below.
# ---------------------------------------------------------------------------

# A question is a question, not a payload. The agent puts this straight into a
# model context that is billed per token, and a 10 MB "question" is either a
# mistake or an attack — never a user.
QUESTION_MAX_CHARS: int = 4_000
REPO_URL_MAX_CHARS: int = 500

# Whole-request ceiling, checked before the body is parsed. Both endpoints take
# a small JSON object; the largest legitimate body is a max-length question.
MAX_REQUEST_BYTES: int = 64 * 1024

# `GET /repos/{id}/files` line-range cap. The viewer renders a window, not a
# 10_000-line file, and an unbounded range is the same response as no range.
FILE_RANGE_MAX_LINES: int = 5_000

# ---------------------------------------------------------------------------
# Job leases — SPEC §15 (v2 phase V3)
# ---------------------------------------------------------------------------

# How stale a lease may get before the sweep reclaims its snapshot.
#
# Far tighter than ZOMBIE_AFTER_S (1200s) can safely be, and that is the point:
# the old sweep leaned on *progress* writes, which are incidental — `linking`
# writes its status once and then runs Jedi silently — so the window had to be
# long enough to cover the quietest legitimate phase. A heartbeat is
# unconditional, so a dead worker's job comes back in two minutes rather than
# twenty (§15.1, §15.4).
LEASE_EXPIRY_S: int = 120

# Heartbeat cadence. Must divide LEASE_EXPIRY_S with room for several missed
# beats: one slow write, or one GC pause, must not look like a dead worker.
HEARTBEAT_EVERY_S: int = 20

# ---------------------------------------------------------------------------
# Identity & tenancy — SPEC §13.8 (v2 phase V1)
# ---------------------------------------------------------------------------

SESSION_TTL_S: int = 14 * 24 * 3_600
OAUTH_STATE_TTL_S: int = 600
SESSION_COOKIE: str = "session"
OAUTH_STATE_COOKIE: str = "oauth_state"

GITHUB_AUTHORIZE_URL: str = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL: str = "https://github.com/login/oauth/access_token"
GITHUB_API_USER_URL: str = "https://api.github.com/user"

# Minimal on purpose (§13.8): V1 reads an identity and nothing else. Private
# repo cloning would need `repo`, which every user sees on the consent screen —
# a v3 decision, not a quiet default.
GITHUB_SCOPES: str = "read:user"


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

    # Identity (SPEC §13). All optional so the app still boots without them —
    # an unconfigured deployment must fail at /auth/github/login with a clear
    # message, not at import, which would take the whole API down over a
    # feature most operators configure second.
    GITHUB_CLIENT_ID: str | None = None
    GITHUB_CLIENT_SECRET: str | None = None
    # Signs session tokens (§13.4). Rotating it invalidates every session,
    # which is the intended emergency lever.
    SESSION_SECRET: str | None = None
    # Adopts the pre-auth repo rows on first sign-in (§13.7).
    BOOTSTRAP_GITHUB_ID: int | None = None

    # Inference service (SPEC §16.3). Unset means load the model in-process,
    # which is what keeps local development and the CLIs working with nothing
    # extra to run — the remote path is opt-in, not a new requirement.
    #
    # Set this on API replicas so they stop carrying torch; leave it unset on
    # ingest workers, whose `token_len` calls are per-chunk and would become a
    # network round trip each (§16.3).
    INFERENCE_URL: str | None = None
    # Generous, because a cold service loads a model before answering and an
    # ingest batch of 256 texts is a real forward pass, not a lookup.
    INFERENCE_TIMEOUT_S: float = 120.0

    @property
    def auth_configured(self) -> bool:
        """Whether the OAuth flow can actually run."""
        return bool(
            self.GITHUB_CLIENT_ID and self.GITHUB_CLIENT_SECRET and self.SESSION_SECRET
        )

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

    # Browser origin(s) allowed by the CORS middleware (SPEC §8). Set before the
    # frontend exists so Phase 5 does not open on a preflight failure. Accepts a
    # comma-separated list: a forwarded dev port (VS Code / Codespaces) reaches
    # the browser as a different host and port than the container binds, and
    # CORS matches origins as exact strings.
    FRONTEND_ORIGIN: str = "http://localhost:3000"

    # Optional regex alternative to listing origins one by one. A forwarded dev
    # port is picked by the editor and changes between sessions, so pinning exact
    # ports means editing .env every time one moves. Unset by default: a
    # deployment allows only what FRONTEND_ORIGIN names.
    FRONTEND_ORIGIN_REGEX: str | None = None

    @property
    def frontend_origins(self) -> list[str]:
        """`FRONTEND_ORIGIN` split into the list the CORS middleware wants."""
        return [o.strip() for o in self.FRONTEND_ORIGIN.split(",") if o.strip()]

    # Cross-encoder rerank — OFF by default (SPEC §5.3, DECISIONS 2026-07-26).
    # Measured worse-or-equal than plain fusion at every k and at MRR, in both
    # corpus conditions, for 2.4 GB of resident model. Kept wired and lazily
    # loaded so the ablation stays permanently measurable
    # (`scripts/eval.py --mode hybrid+rerank`).
    RERANK_ENABLED: bool = False

    # -----------------------------------------------------------------------
    # Serving: pool sizing, timeouts, limits (DECISIONS 2026-07-28).
    #
    # Everything below has to be sized against the machine it runs on, which is
    # why these are env-tunable and the §12/contract numbers above are not.
    # -----------------------------------------------------------------------

    # asyncpg defaults to min=max=10, which is invisible until the day it is the
    # bottleneck. Named here so the number is a decision rather than an accident.
    DB_POOL_MIN_SIZE: int = 5
    DB_POOL_MAX_SIZE: int = 20
    # Per-statement ceiling for the API. A query that runs longer than this is
    # holding a pooled connection hostage; failing it is cheaper than the queue
    # it builds behind itself. The worker overrides this — batch inserts of a
    # few thousand embeddings legitimately take longer.
    DB_COMMAND_TIMEOUT_S: float = 30.0
    DB_POOL_MAX_IDLE_S: float = 300.0

    # Concurrent SSE chat streams. Each one is an agent loop: up to 8 tool
    # calls, N provider round-trips, and real money. Past this the API answers
    # 429 immediately rather than accepting work it will serve badly.
    CHAT_MAX_CONCURRENCY: int = 8
    # Wall-clock ceiling for one agent run. The 8-call cap bounds tool count,
    # not time — a wedged provider is unbounded without this.
    CHAT_TIMEOUT_S: float = 180.0
    # Per-request provider timeout, passed to whichever client model.py builds.
    AGENT_REQUEST_TIMEOUT_S: float = 60.0

    # Concurrent ingests (queued or in flight). Each is minutes of CPU and
    # hundreds of MB of disk on a box that is also serving chat.
    MAX_ACTIVE_INGESTS: int = 3

    # Per-IP rate limits, enforced in Redis (app/api/ratelimit.py). Windows are
    # in seconds; the defaults are deliberately generous for reads and tight for
    # the two endpoints that spend CPU and money.
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_INGEST_PER_HOUR: int = 10
    RATE_LIMIT_CHAT_PER_HOUR: int = 60
    RATE_LIMIT_DEFAULT_PER_MINUTE: int = 120

    # Whether X-Forwarded-For may be believed when identifying a client. OFF by
    # default: behind no proxy the header is attacker-controlled, and trusting
    # it turns every rate limit into a suggestion. Turn on only when a proxy you
    # control is the sole path to this process.
    TRUST_PROXY_HEADERS: bool = False

    # Threads available to CPU-bound model inference. Sized for a 4-core box:
    # two concurrent forward passes, each pinned to two intra-op threads, leaves
    # the event loop a core to run on. Oversubscribing here makes every request
    # slower, not just the extra ones.
    INFERENCE_THREADS: int = 2
    TORCH_NUM_THREADS: int | None = 2

    # Observability.
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "text"  # "text" for humans, "json" for log shipping
    METRICS_ENABLED: bool = True
    # When set, /metrics requires `Authorization: Bearer <token>`. Unset means
    # open — fine behind a private network, not fine on a public URL.
    METRICS_TOKEN: str | None = None


@lru_cache
def get_settings() -> Settings:
    """Return a cached `Settings` instance."""
    return Settings()  # type: ignore[call-arg]  # values sourced from env/.env
