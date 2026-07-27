"""Typed exceptions raised by service-layer code.

Only the API layer maps these to HTTP responses (CLAUDE.md conventions).
Phase 1 defines the ingestion-related errors; later phases append their own.
"""

from __future__ import annotations


class AppError(Exception):
    """Base class for all application-level errors."""


class IngestError(AppError):
    """Base class for ingestion-pipeline failures."""


class CloneError(IngestError):
    """A repository could not be cloned (bad URL, network, git failure)."""


class TooManyFilesError(IngestError):
    """The selected-file set exceeds ``MAX_FILES`` (SPEC §2.2 step 7)."""

    def __init__(self, count: int, limit: int) -> None:
        self.count = count
        self.limit = limit
        super().__init__(
            f"repository has {count} candidate files, exceeding the "
            f"limit of {limit}"
        )


class InvalidRepoUrlError(IngestError):
    """The submitted URL is not a usable public GitHub repository URL (§8, 422)."""


class RepoNotFoundError(AppError):
    """No repo row for the requested id (§8, 404)."""

    def __init__(self, repo_id: object) -> None:
        self.repo_id = repo_id
        super().__init__(f"no repo {repo_id}")


class RepoFileNotFoundError(AppError):
    """The repo exists but holds no file at that path (§8, 404).

    Named to avoid shadowing the builtin ``FileNotFoundError``, which means
    something else entirely (a missing path on this machine's disk).
    """

    def __init__(self, path: str) -> None:
        self.path = path
        super().__init__(f"no file {path!r} in this repo")


class RepoNotReadyError(AppError):
    """Chat was requested on a repo that is still indexing or failed (§8, 409).

    Carries the current status so the API can put it in the response body — the
    frontend distinguishes "come back in a minute" from "this one failed".
    """

    def __init__(self, status: str) -> None:
        self.status = status
        super().__init__(f"repo not ready (status={status})")


class QueueUnavailableError(AppError):
    """Redis could not be reached, so no ingest job can be enqueued (§8, 503).

    Ingestion never runs inside the HTTP handler (CLAUDE.md hard rule 1), so an
    unreachable queue is a hard failure, not something to work around inline.
    """


class AgentError(AppError):
    """Base class for agent-layer failures (SPEC §7).

    Raised for configuration problems the model cannot recover from — an
    unset ``AGENT_MODEL``, a missing provider key. Tool failures are *not*
    exceptions: they return ``{"error": ...}`` so the loop keeps going
    (SPEC §7.1).
    """
