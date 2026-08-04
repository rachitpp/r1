"""File selection (SPEC §2.2, §30.2). All ingestion filter logic lives here only.

Numbers come from ``app.config`` (SPEC §12); none are hardcoded here. Steps
run in the SPEC order: git-tracked candidates -> ``*.py`` **or a prose/config
path** -> ignore-dir segments -> size cap -> binary sniff -> UTF-8 decode ->
``MAX_FILES`` guard.

Only step 2 changed for §30, and only by widening: everything after it applies
to a README exactly as it applied to a module. ``IGNORE_DIRS`` does real work
here now — without it, `node_modules` and vendored docs would arrive as prose.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from git import Repo

from app.config import (
    CI_WORKFLOW_DIR,
    CI_WORKFLOW_EXTENSIONS,
    CONFIG_EXTENSIONS,
    CONFIG_FILENAMES,
    CONFIG_NAME_PREFIXES,
    IGNORE_DIRS,
    MAX_FILE_BYTES,
    MAX_FILES,
    PROSE_EXTENSIONS,
    TEST_DIR_SEGMENTS,
    TEST_FILE_NAMES,
)
from app.exceptions import TooManyFilesError

logger = logging.getLogger(__name__)

_BINARY_SNIFF_BYTES = 8 * 1024  # first 8 KB, per SPEC §2.2 step 5

# What a selected file will be chunked as. `code` takes the tree-sitter path;
# the other two take the §30.3 prose path and become `chunks.kind`.
FileClass = Literal["code", "document", "config"]

# The classes §30.4 keeps out of the default retrieval pool.
PROSE_CLASSES: frozenset[str] = frozenset({"document", "config"})


@dataclass(frozen=True)
class SourceFile:
    """A file that survived selection, ready for parsing."""

    path: str  # repo-relative, posix-style (forward slashes)
    text: str
    n_lines: int
    # `code` for everything selected before §30, which is why it defaults: the
    # naive baseline (§2.7) and every existing test construct `SourceFile`
    # directly and mean Python.
    file_class: FileClass = "code"

    @property
    def is_prose(self) -> bool:
        """Whether §30.4 excludes this file's chunks from the default pool."""
        return self.file_class in PROSE_CLASSES


@dataclass(frozen=True)
class SelectionResult:
    """Surviving files plus per-reason skip counts for the CLI stats block."""

    files: list[SourceFile]
    n_candidates: int
    skipped_unsupported: int
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


def classify_path(posix_path: str) -> FileClass | None:
    """What class ``posix_path`` is ingested as, or ``None`` to skip it (§30.2).

    Order is the whole rule. ``*.py`` wins outright, so `setup.py` and
    `noxfile.py` stay code — §30.2 lists them only to say they are *not*
    reclassified. A file is code or it is prose, and the extension decides.

    Config is checked before prose so `requirements-dev.txt` lands as `config`
    rather than as a `.txt` document: it has no headings to chunk on, and
    splitting it would answer nothing.
    """
    path = PurePosixPath(posix_path)
    name = path.name
    suffix = path.suffix

    if suffix == ".py":
        return "code"

    # CI workflows are matched on their directory — `.github/workflows` holds
    # nothing else, and its files are named freely.
    if posix_path.startswith(f"{CI_WORKFLOW_DIR}/") and suffix in CI_WORKFLOW_EXTENSIONS:
        return "config"

    if name in CONFIG_FILENAMES or suffix in CONFIG_EXTENSIONS:
        return "config"
    if any(name.startswith(prefix) for prefix in CONFIG_NAME_PREFIXES):
        return "config"

    if suffix in PROSE_EXTENSIONS:
        return "document"
    return None


def is_test_path(posix_path: str) -> bool:
    """Whether ``posix_path`` is test code, per the SPEC §2.6 corpus-wide rule.

    True when any path segment is in ``TEST_DIR_SEGMENTS`` or the filename is
    ``test_*.py`` / ``*_test.py`` / one of ``TEST_FILE_NAMES``. Deliberately a
    flat path rule with no per-file judgment: test files are kept in the corpus
    but flagged, so retrieval can target implementation by default
    (DECISIONS 2026-07-26, "test shadowing"). Selection is unaffected — this
    classifies, it does not exclude.
    """
    path = PurePosixPath(posix_path)
    if any(part in TEST_DIR_SEGMENTS for part in path.parts[:-1]):
        return True
    name = path.name
    if name in TEST_FILE_NAMES:
        return True
    return name.startswith("test_") or name.endswith("_test.py")


def select_files(repo_dir: Path) -> SelectionResult:
    """Select ingestible files from ``repo_dir`` per SPEC §2.2.

    Raises :class:`TooManyFilesError` if survivors exceed ``MAX_FILES``.
    """
    candidates = _tracked_paths(repo_dir)
    n_candidates = len(candidates)

    skipped_unsupported = 0
    skipped_ignored_dir = 0
    skipped_too_large = 0
    skipped_binary = 0
    skipped_decode_error = 0

    survivors: list[SourceFile] = []
    for rel in candidates:
        # 2. Keep *.py, or a §30.2 prose/config path. Every later step —
        #    ignore-dirs, size cap, binary sniff, decode, MAX_FILES — is
        #    unchanged and applies to all of them alike.
        file_class = classify_path(rel)
        if file_class is None:
            skipped_unsupported += 1
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
        survivors.append(
            SourceFile(path=rel, text=text, n_lines=n_lines, file_class=file_class)
        )

    # 7. Abort if survivors exceed MAX_FILES.
    if len(survivors) > MAX_FILES:
        raise TooManyFilesError(len(survivors), MAX_FILES)

    return SelectionResult(
        files=survivors,
        n_candidates=n_candidates,
        skipped_unsupported=skipped_unsupported,
        skipped_ignored_dir=skipped_ignored_dir,
        skipped_too_large=skipped_too_large,
        skipped_binary=skipped_binary,
        skipped_decode_error=skipped_decode_error,
    )
