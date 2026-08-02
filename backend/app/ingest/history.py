"""Walk a clone's commit log into storable rows (SPEC §20).

One ``git log`` invocation, parsed. The obvious alternative — GitPython's
``repo.iter_commits()`` with ``commit.stats`` — is a separate diff per commit,
so 500 commits is 500 subprocesses on top of the object reads. This is one.

The parse is the only interesting part, and it exists because commit *bodies*
contain newlines. Three control characters make it unambiguous rather than
heuristic: a record separator introduces each commit, unit separators delimit
the header fields, and — the one that matters — an **explicit terminator after
``%b``** ends the body. git's ``--format`` passes literal characters through, so
the body is everything up to that byte and the ``--numstat`` block is everything
after it.

The first version instead scanned backwards from the end of the record while
lines still looked like numstat, which lost a body whose *final* line was
exactly ``<int>\\t<int>\\t<path>``. That was written up as an accepted
limitation before the terminator turned out to cost one character.
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
# ETX, closing the body. Without it the body and the numstat block that follows
# are separated only by a guess, because `%b` is the one field that can contain
# newlines. With it the split is exact.
_BODY_END = "\x03"

_FORMAT = (
    _REC + _FIELD.join(["%H", "%an", "%ae", "%aI", "%P", "%s", "%b"]) + _BODY_END
)

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
    # The terminator splits header+body from the file block exactly. A record
    # without one is truncated output, not something to guess at.
    head, sep, numstat_block = record.partition(_BODY_END)
    if not sep:
        return None

    parts = head.split(_FIELD)
    if len(parts) < 7:
        return None
    sha, author_name, author_email, authored, parents, subject = parts[:6]
    # Re-join rather than take parts[6]: a US byte in a commit body would
    # otherwise silently truncate it. Nothing after the subject is delimited.
    body = _FIELD.join(parts[6:]).strip() or None

    try:
        when = datetime.fromisoformat(authored.strip())
    except ValueError:
        return None

    files: list[CommitFileRow] = []
    for line in numstat_block.split("\n"):
        m = _NUMSTAT.match(line)
        if not m:
            continue
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
    repo_path: Path,
    max_commits: int = HISTORY_MAX_COMMITS,
    rev: str | None = None,
) -> tuple[list[CommitRow], list[CommitFileRow]]:
    """Read up to ``max_commits`` commits from the clone at ``repo_path``.

    ``rev`` walks from a specific commit instead of HEAD. Ingest does not need
    it — it walks the clone it just made — but backfilling an existing snapshot
    does: that snapshot is pinned to `commit_sha` (§14), and the repo's HEAD has
    moved on since. Walking HEAD there would file another commit's history under
    this snapshot's id, which is worse than having none.

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
            *([rev] if rev else []),
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
