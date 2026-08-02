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


class ConversationNotFoundError(AppError):
    """No conversation for that id, for this caller, on this snapshot (§23, 404).

    One status for "never existed", "someone else's" and "a different repo's" —
    §13.5's reasoning, and here it also stops the id being a probe for which
    conversations exist.
    """

    def __init__(self, conversation_id: object) -> None:
        self.conversation_id = conversation_id
        super().__init__(f"no conversation {conversation_id}")


class SharedAnswerNotFoundError(AppError):
    """No permalink for that id (§21.3, 404).

    Raised identically for "never existed", "belongs to someone else" and
    "retracted". Collapsing the three is the point: a distinguishable response
    would let anyone probe which share ids are real.
    """

    def __init__(self, share_id: object) -> None:
        self.share_id = share_id
        super().__init__(f"no shared answer {share_id}")


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


class AgentTimeoutError(AgentError):
    """An agent run exceeded ``CHAT_TIMEOUT_S``.

    The §7.2 tool cap bounds how many calls a run may make, not how long it may
    take; a provider that stops responding mid-stream is unbounded without a
    wall clock. Surfaces as an ``error`` SSE event, not an HTTP status — by the
    time it fires, the response headers are long gone.
    """


class TooManyRequestsError(AppError):
    """The caller is over a limit and should come back later (429).

    ``retry_after`` is seconds, and lands in the ``Retry-After`` header: a 429
    that does not say when to retry invites the exact hammering it exists to
    stop. ``rule`` names which limit tripped, for the metric label and the logs.
    """

    def __init__(self, message: str, *, retry_after: int, rule: str) -> None:
        self.retry_after = retry_after
        self.rule = rule
        super().__init__(message)


class ServiceBusyError(TooManyRequestsError):
    """Capacity, not quota: every concurrency slot for this work is taken.

    Distinct from a rate limit because the caller did nothing wrong — the
    process is simply full, and accepting the work would mean serving it (and
    everything already in flight) badly.
    """


class PayloadTooLargeError(AppError):
    """A request body exceeds ``MAX_REQUEST_BYTES`` (413)."""

    def __init__(self, size: int, limit: int) -> None:
        self.size = size
        self.limit = limit
        super().__init__(f"request body is {size} bytes, over the {limit}-byte limit")


class InvalidLineRangeError(AppError):
    """A ``start_line``/``end_line`` pair that describes no lines (422)."""

    def __init__(self, start: int, end: int) -> None:
        self.start = start
        self.end = end
        super().__init__(f"line range {start}-{end} ends before it starts")


class UnauthorizedError(AppError):
    """A protected endpoint was called without the right credentials (401)."""


class SnapshotSuperseded(AppError):
    """**Not a failure.** The clone revealed this commit is already ingested.

    Raised by the pipeline once the SHA is known and an existing `ready`
    snapshot of the same `(source, commit, strategy)` is found (SPEC §14.4).
    The redundant snapshot's library entries are moved to the existing one and
    its row is deleted before this is raised, so by the time a caller sees it
    the work is done — there is simply nothing left to ingest.

    An exception rather than a return value because it unwinds from the middle
    of the clone context, several frames below the caller that cares, and every
    layer in between would otherwise have to thread a sentinel through. It
    subclasses `AppError` so nothing catches it as an infrastructure fault; the
    worker and the CLI both treat it as a successful outcome.
    """

    def __init__(self, kept_id: object) -> None:
        self.kept_id = kept_id
        super().__init__(f"already ingested at this commit; using snapshot {kept_id}")


class AuthNotConfiguredError(AppError):
    """Sign-in was attempted without OAuth credentials in the environment (503).

    Deliberately a runtime failure on the auth routes rather than a startup
    one: the API serves an already-signed-in user, `/health`, and `/ready`
    perfectly well without GitHub credentials, and refusing to boot over a
    missing optional secret takes the whole service down for a feature most
    operators configure second (SPEC §13).
    """


class OAuthError(AppError):
    """The GitHub OAuth exchange failed or was tampered with (§13.3, 400).

    Covers a `state` mismatch, a denied consent screen, and a token exchange
    GitHub rejected. One exception for all three on purpose — the difference
    matters to the log, never to the caller, and a response that distinguishes
    them tells a probe which half of the flow it broke.
    """
