"""Walk a clone's commit log into storable rows (SPEC §20).

One ``git log`` invocation, parsed. The obvious alternative — GitPython's
``repo.iter_commits()`` with ``commit.stats`` — is a separate diff per commit,
so 500 commits is 500 subprocesses on top of the object reads. This is one.

The parse is the only interesting part, and it exists because commit *bodies*
contain newlines. Each record is introduced by a record separator and its
fields are unit-separated, so the header is unambiguous; the trailing field is
``body`` followed by the ``--numstat`` block, which is disambiguated by
scanning from the end while lines still look like numstat. A body whose final
line is exactly ``<int>\\t<int>\\t<path>`` would lose that line to the file
list — accepted, because the alternative is two passes over the log to save a
case that does not occur in practice.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from git import Repo

from app.config import HISTORY_MAX_COMMITS

logger = logging.getLogger(__name__)

# ASCII record/unit separators: control characters git will never emit inside a
# field, which is what keeps this from needing an escaping scheme.
_REC = "\x1e"
_FIELD = "\x1f"

_FORMAT = _REC + _FIELD.join(["%H", "%an", "%ae", "%aI", "%P", "%s", "%b"])

# `<insertions>\t<deletions>\t<path>`, where a binary file reports `-` for both.
_NUMSTAT = re.compile(r"^(\d+|-)\t(\d+|-)\t(.+)$")

# Renames arrive as `src/{old => new}.py` or `old.py => new.py`. We want the
# path as it exists at HEAD, which is the right-hand side.
_RENAME_BRACED = re.compile(r"\{([^{}]*) => ([^{}]*)\}")


@dataclass(frozen=True)
class CommitRow:
    """One commit, already split into the fields §20.1 stores."""

    sha: str
    author_name: str
    author_email: str | None
    authored_at: datetime
    subject: str
    body: str | None
    is_merge: bool


@dataclass(frozen=True)
class CommitFileRow:
    """One (commit, path) touch with its line deltas."""

    sha: str
    file_path: str
    insertions: int
    deletions: int


def normalise_path(raw: str) -> str:
    """Resolve git's rename notation to the post-rename path.

    ``src/{a => b}.py`` -> ``src/b.py``; ``a.py => b.py`` -> ``b.py``. A path
    with no rename marker is returned unchanged.
    """
    if "{" in raw and " => " in raw:
        collapsed = _RENAME_BRACED.sub(lambda m: m.group(2), raw)
        # `{old => }` collapses to a doubled slash when a file moves up a level.
        return collapsed.replace("//", "/")
    if " => " in raw:
        return raw.split(" => ", 1)[1]
    return raw


def _parse_record(record: str) -> tuple[CommitRow, list[CommitFileRow]] | None:
    """Turn one separator-delimited record into rows, or None if malformed."""
    parts = record.split(_FIELD)
    if len(parts) < 7:
        return None
    sha, author_name, author_email, authored, parents, subject = parts[:6]
    tail = parts[6]

    try:
        when = datetime.fromisoformat(authored.strip())
    except ValueError:
        return None

    # Split the trailing field into body and numstat by walking backwards: the
    # file block is always last and always contiguous.
    lines = tail.split("\n")
    files: list[CommitFileRow] = []
    cut = len(lines)
    for i in range(len(lines) - 1, -1, -1):
        line = lines[i]
        if not line.strip():
            # A blank line inside the file block cannot happen, but one between
            # body and block can — keep scanning without consuming it.
            if i == len(lines) - 1 or files:
                cut = i
                continue
            break
        m = _NUMSTAT.match(line)
        if not m:
            break
        ins, dels, raw_path = m.groups()
        files.append(
            CommitFileRow(
                sha=sha,
                file_path=normalise_path(raw_path),
                # Binary files report `-`; 0 is the honest number of *lines*.
                insertions=0 if ins == "-" else int(ins),
                deletions=0 if dels == "-" else int(dels),
            )
        )
        cut = i
    files.reverse()

    body = "\n".join(lines[:cut]).strip() or None
    return (
        CommitRow(
            sha=sha,
            author_name=author_name or "unknown",
            author_email=author_email or None,
            authored_at=when,
            subject=subject,
            body=body,
            # A merge has more than one parent. `--numstat` emits no file lines
            # for one by default, which is why merges cost nothing here and are
            # still visible in a repo-wide timeline.
            is_merge=len(parents.split()) > 1,
        ),
        files,
    )


def walk_history(
    repo_path: Path, max_commits: int = HISTORY_MAX_COMMITS
) -> tuple[list[CommitRow], list[CommitFileRow]]:
    """Read up to ``max_commits`` commits from the clone at ``repo_path``.

    Never raises for history reasons. A repo with one commit, a shallow clone
    that cannot walk further, an empty log — all return what they have. History
    is an enrichment: failing an ingest that already produced a working corpus
    over it would trade something real for something optional.
    """
    try:
        repo = Repo(repo_path)
        raw = repo.git.log(
            f"--format={_FORMAT}",
            "--numstat",
            f"--max-count={max_commits}",
            # Dates as authored, not normalised to the ingesting machine's zone.
            "--date=iso-strict",
        )
    except Exception as exc:  # noqa: BLE001 - deliberately total; see docstring
        # GitCommandError is the expected one, but "not a repo", a corrupt
        # shallow graft and an unreadable object all land here too, and every
        # one of them should cost history rather than the ingest.
        logger.warning("history walk failed for %s: %s", repo_path, exc)
        return [], []

    commits: list[CommitRow] = []
    touches: list[CommitFileRow] = []
    for record in raw.split(_REC):
        if not record.strip():
            continue
        parsed = _parse_record(record)
        if parsed is None:
            continue
        commit, files = parsed
        commits.append(commit)
        touches.extend(files)

    return commits, touches
