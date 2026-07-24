"""File selection (SPEC §2.2). All ingestion filter logic lives here only.

Numbers come from ``app.config`` (SPEC §12); none are hardcoded here. Steps
run in the SPEC order: git-tracked candidates -> ``*.py`` -> ignore-dir
segments -> size cap -> binary sniff -> UTF-8 decode -> ``MAX_FILES`` guard.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from git import Repo

from app.config import IGNORE_DIRS, MAX_FILE_BYTES, MAX_FILES
from app.exceptions import TooManyFilesError

logger = logging.getLogger(__name__)

_BINARY_SNIFF_BYTES = 8 * 1024  # first 8 KB, per SPEC §2.2 step 5


@dataclass(frozen=True)
class SourceFile:
    """A file that survived selection, ready for parsing."""

    path: str  # repo-relative, posix-style (forward slashes)
    text: str
    n_lines: int


@dataclass(frozen=True)
class SelectionResult:
    """Surviving files plus per-reason skip counts for the CLI stats block."""

    files: list[SourceFile]
    n_candidates: int
    skipped_non_python: int
    skipped_ignored_dir: int
    skipped_too_large: int
    skipped_binary: int
    skipped_decode_error: int

    @property
    def n_kept(self) -> int:
        return len(self.files)


def _tracked_paths(repo_dir: Path) -> list[str]:
    """Return git-tracked paths (posix-style), inheriting .gitignore for free."""
    repo = Repo(repo_dir)
    # ``git ls-files`` lists tracked files relative to the repo root, already
    # forward-slashed regardless of platform.
    output = repo.git.ls_files()
    return [line for line in output.splitlines() if line]


def _has_ignored_segment(posix_path: str) -> bool:
    return any(part in IGNORE_DIRS for part in PurePosixPath(posix_path).parts)


def select_files(repo_dir: Path) -> SelectionResult:
    """Select ingestible files from ``repo_dir`` per SPEC §2.2.

    Raises :class:`TooManyFilesError` if survivors exceed ``MAX_FILES``.
    """
    candidates = _tracked_paths(repo_dir)
    n_candidates = len(candidates)

    skipped_non_python = 0
    skipped_ignored_dir = 0
    skipped_too_large = 0
    skipped_binary = 0
    skipped_decode_error = 0

    survivors: list[SourceFile] = []
    for rel in candidates:
        # 2. Keep only *.py.
        if not rel.endswith(".py"):
            skipped_non_python += 1
            continue
        # 3. Drop paths with an IGNORE_DIRS segment at any depth.
        if _has_ignored_segment(rel):
            skipped_ignored_dir += 1
            continue

        abs_path = repo_dir / rel
        try:
            raw = abs_path.read_bytes()
        except OSError as exc:  # broken symlink, race with cleanup, etc.
            logger.warning("could not read %s: %s", rel, exc)
            skipped_decode_error += 1
            continue

        # 4. Drop files over the size cap.
        if len(raw) > MAX_FILE_BYTES:
            skipped_too_large += 1
            continue
        # 5. Binary sniff: null byte in the first 8 KB.
        if b"\x00" in raw[:_BINARY_SNIFF_BYTES]:
            skipped_binary += 1
            continue
        # 6. UTF-8 decode; skip with a warning on failure.
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            logger.warning("skipping %s: not valid UTF-8", rel)
            skipped_decode_error += 1
            continue

        n_lines = text.count("\n") + (0 if text.endswith("\n") or not text else 1)
        survivors.append(SourceFile(path=rel, text=text, n_lines=n_lines))

    # 7. Abort if survivors exceed MAX_FILES.
    if len(survivors) > MAX_FILES:
        raise TooManyFilesError(len(survivors), MAX_FILES)

    return SelectionResult(
        files=survivors,
        n_candidates=n_candidates,
        skipped_non_python=skipped_non_python,
        skipped_ignored_dir=skipped_ignored_dir,
        skipped_too_large=skipped_too_large,
        skipped_binary=skipped_binary,
        skipped_decode_error=skipped_decode_error,
    )
