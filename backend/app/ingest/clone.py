"""Shallow-clone a public GitHub repo into an ephemeral work dir (SPEC §2.1).

Depth-1, single-branch clone; the directory is always removed afterwards,
including on failure. Windows note: git packs object files read-only, so
``shutil.rmtree`` needs a handler that clears the read-only bit before
retrying the delete.
"""

from __future__ import annotations

import contextlib
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

from app.exceptions import CloneError


@dataclass(frozen=True)
class CloneInfo:
    """Result of a successful clone."""

    path: Path
    head_sha: str
    default_branch: str
    name: str  # "owner/repo"


def _repo_name_from_url(url: str) -> str:
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
    """Remove a tree, tolerating Windows read-only git object files."""
    if not path.exists():
        return
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_on_rm_error)
    else:  # pragma: no cover - exercised only on <3.12 interpreters
        shutil.rmtree(path, onerror=_on_rm_error)


def clone_repo(url: str) -> CloneInfo:
    """Shallow-clone ``url`` into a fresh temp dir and return its metadata.

    The caller owns the returned directory and must delete it; prefer the
    :func:`cloned_repo` context manager, which guarantees cleanup.
    """
    workdir = Path(tempfile.mkdtemp(prefix="onboarding-clone-"))
    try:
        repo = Repo.clone_from(
            url,
            workdir,
            multi_options=["--depth", "1", "--single-branch"],
        )
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
            name=_repo_name_from_url(url),
        )
    except GitCommandError as exc:
        _rmtree(workdir)
        raise CloneError(f"failed to clone {url}: {exc}") from exc
    except Exception:
        _rmtree(workdir)
        raise


@contextlib.contextmanager
def cloned_repo(url: str) -> Iterator[CloneInfo]:
    """Context manager that clones ``url`` and always removes the work dir."""
    info = clone_repo(url)
    try:
        yield info
    finally:
        _rmtree(info.path)
