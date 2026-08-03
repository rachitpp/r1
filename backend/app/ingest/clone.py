"""Shallow-clone a public GitHub repo into an ephemeral work dir (SPEC §2.1).

Depth-1, single-branch clone; the directory is always removed afterwards,
including on failure. Windows note: git packs object files read-only, so
``shutil.rmtree`` needs a handler that clears the read-only bit before
retrying the delete.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import shutil
import stat
import sys
import tempfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from git import GitCommandError, Repo

from app.config import HISTORY_MAX_COMMITS
from app.exceptions import CloneError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CloneInfo:
    """Result of a successful clone."""

    path: Path
    head_sha: str
    default_branch: str
    name: str  # "owner/repo"


def repo_name_from_url(url: str) -> str:
    """Derive an ``owner/repo`` name from a GitHub URL (https or scp form)."""
    trimmed = url.rstrip("/")
    if trimmed.endswith(".git"):
        trimmed = trimmed[: -len(".git")]
    # Split on both "/" and ":" so scp-style (git@github.com:owner/repo) and
    # https URLs both reduce to their trailing owner/repo pair.
    parts = [p for p in re.split(r"[:/]+", trimmed) if p]
    if len(parts) >= 2:
        return f"{parts[-2]}/{parts[-1]}"
    return parts[-1] if parts else url


def _on_rm_error(
    func: Callable[[str], Any], path: str, exc_info: object
) -> None:
    """rmtree error handler: clear the read-only bit and retry once.

    Compatible with both the ``onexc`` (3.12+) and ``onerror`` (<3.12)
    ``shutil.rmtree`` callback signatures — the third argument differs but is
    unused here.
    """
    os.chmod(path, stat.S_IWRITE)
    func(path)


def _rmtree(path: Path) -> None:
    """Remove a tree, tolerating Windows read-only git object files.

    **Best effort, never fatal.** This runs in the ``finally`` of
    :func:`cloned_repo`, so an exception here replaces whatever the block
    produced — turning a *completed* ingest into a failure and writing a
    ``failed`` snapshot for a corpus that is already safely in Postgres. The
    clone is scratch; the database is the durable copy (DECISIONS 2026-07-24).

    Windows makes this a live concern rather than a theoretical one: git marks
    objects read-only, and a virus scanner or indexer can hold a handle briefly
    after the process exits, so even the chmod-and-retry above can lose. A
    leaked temp directory is a nuisance the OS eventually clears; a lost ingest
    is not.
    """
    if not path.exists():
        return
    try:
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=_on_rm_error)
        else:  # pragma: no cover - exercised only on <3.12 interpreters
            shutil.rmtree(path, onerror=_on_rm_error)
    except OSError as exc:
        logger.warning("could not remove clone workdir %s: %s", path, exc)


def clone_repo(url: str, rev: str | None = None) -> CloneInfo:
    """Shallow-clone ``url`` into a fresh temp dir and return its metadata.

    The caller owns the returned directory and must delete it; prefer the
    :func:`cloned_repo` context manager, which guarantees cleanup.

    **Depth is ``HISTORY_MAX_COMMITS``, not 1.** §2.1 cloned depth-1 because
    history was out of v1 scope; §20 puts it back in, and a depth-1 clone has
    exactly one commit to walk. The clone stays *shallow* — this is a bounded
    deepening, not a full history — so the cost is one number's worth of commit
    objects, not a repo's entire past. Blobs are still fetched only for the
    checked-out tree, which is where clone time actually goes.

    ``rev`` checks out a specific commit instead of the branch tip, which is
    what makes §28 comparison possible at all: comparing a repo against its own
    past needs two snapshots at two commits, and until this existed every
    snapshot was pinned to whatever HEAD happened to be on the day it ran.

    The rev must be inside the shallow window (``HISTORY_MAX_COMMITS``). A
    deeper one is reported as a `CloneError` naming the depth rather than
    something opaque from git — "not found" is a confusing thing to be told
    about a commit that plainly exists.
    """
    workdir = Path(tempfile.mkdtemp(prefix="onboarding-clone-"))
    try:
        repo = Repo.clone_from(
            url,
            workdir,
            multi_options=[
                "--depth",
                str(HISTORY_MAX_COMMITS),
                "--single-branch",
            ],
        )
        if rev is not None:
            try:
                repo.git.checkout(rev)
            except GitCommandError as exc:
                raise CloneError(
                    f"could not check out {rev!r} in {url}: it is not within the "
                    f"most recent {HISTORY_MAX_COMMITS} commits of the default "
                    f"branch, which is all a shallow clone fetches ({exc})"
                ) from exc
        head_sha = repo.head.commit.hexsha
        try:
            default_branch = repo.active_branch.name
        except TypeError:
            # Detached HEAD after a shallow clone; fall back to the remote's
            # advertised default branch, else "HEAD".
            default_branch = "HEAD"
            with contextlib.suppress(Exception):
                ref = repo.remotes.origin.refs.HEAD.reference.name
                default_branch = ref.split("/", 1)[-1]
        return CloneInfo(
            path=workdir,
            head_sha=head_sha,
            default_branch=default_branch,
            name=repo_name_from_url(url),
        )
    except GitCommandError as exc:
        _rmtree(workdir)
        raise CloneError(f"failed to clone {url}: {exc}") from exc
    except Exception:
        _rmtree(workdir)
        raise


@contextlib.contextmanager
def cloned_repo(url: str, rev: str | None = None) -> Iterator[CloneInfo]:
    """Context manager that clones ``url`` and always removes the work dir."""
    info = clone_repo(url, rev)
    try:
        yield info
    finally:
        _rmtree(info.path)
