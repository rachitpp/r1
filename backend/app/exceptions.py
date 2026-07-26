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


class AgentError(AppError):
    """Base class for agent-layer failures (SPEC §7).

    Raised for configuration problems the model cannot recover from — an
    unset ``AGENT_MODEL``, a missing provider key. Tool failures are *not*
    exceptions: they return ``{"error": ...}`` so the loop keeps going
    (SPEC §7.1).
    """
