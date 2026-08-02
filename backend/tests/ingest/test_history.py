"""The §20.1 log walk.

Driven against **real git repositories** built in a tmp dir rather than against
canned `git log` output. The whole risk in this module is that the format string
and the parser disagree, and a fixture of hand-written output tests the parser
against my belief about git rather than against git.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.ingest.history import normalise_path, walk_history


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A small repo with the shapes §20.1 has to survive."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "ada@example.com")
    _git(root, "config", "user.name", "Ada")

    (root / "a.py").write_text("x = 1\n")
    _git(root, "add", "a.py")
    _git(root, "commit", "-q", "-m", "first: add a")

    (root / "a.py").write_text("x = 1\ny = 2\n")
    (root / "b.py").write_text("z = 3\n")
    _git(root, "add", ".")
    # A body with blank lines and a line that is *almost* numstat — the parse
    # walks backwards from the end, and this is what would break it.
    _git(
        root,
        "commit",
        "-q",
        "-m",
        "second: touch both\n\nWhy: because.\n\nA table row: 1\t2\tnot-a-path",
    )
    return root


def test_walks_commits_newest_first(repo: Path) -> None:
    commits, _ = walk_history(repo)
    assert [c.subject for c in commits] == ["second: touch both", "first: add a"]


def test_captures_author_and_authored_date(repo: Path) -> None:
    commits, _ = walk_history(repo)
    assert commits[0].author_name == "Ada"
    assert commits[0].author_email == "ada@example.com"
    # tz-aware, so it can go into a TIMESTAMPTZ column without a guess.
    assert commits[0].authored_at.tzinfo is not None


def test_splits_subject_from_body(repo: Path) -> None:
    commits, _ = walk_history(repo)
    newest, oldest = commits
    assert newest.subject == "second: touch both"
    assert newest.body is not None
    assert newest.body.startswith("Why: because.")
    # A commit with no body stores NULL, not "".
    assert oldest.body is None


def test_body_keeps_a_line_that_looks_like_numstat(repo: Path) -> None:
    commits, _ = walk_history(repo)
    assert commits[0].body is not None
    assert "not-a-path" in commits[0].body


def test_body_ending_in_a_numstat_line_is_kept_whole(tmp_path: Path) -> None:
    """The case the first parser lost.

    Scanning backwards for the file block cannot tell a body's last line from
    the block's first. The `%b` terminator can, and this is the regression that
    proves it: the numstat-shaped line is the *final* line of the body, and it
    must stay in the body while the real file list stays out of it.
    """
    root = tmp_path / "trap"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "a@b.c")
    _git(root, "config", "user.name", "A")
    (root / "real.py").write_text("v = 1\n")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "subj\n\nchangelog:\n12\t3\tsrc/fake.py")

    commits, touches = walk_history(root)
    assert commits[0].body == "changelog:\n12\t3\tsrc/fake.py"
    # The decoy never became a file row; only the file actually committed did.
    assert [t.file_path for t in touches] == ["real.py"]


def test_a_unit_separator_in_the_body_does_not_truncate_it(tmp_path: Path) -> None:
    """Fields are US-delimited, and only the body may legitimately contain one."""
    root = tmp_path / "us"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "a@b.c")
    _git(root, "config", "user.name", "A")
    (root / "f.py").write_text("v = 1\n")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "subj\n\nbefore\x1fafter")

    commits, _ = walk_history(root)
    assert commits[0].body is not None
    assert "after" in commits[0].body


def test_file_touches_carry_line_deltas(repo: Path) -> None:
    _, touches = walk_history(repo)
    second = {t.file_path: t for t in touches if t.insertions or t.deletions}
    assert second["b.py"].insertions == 1
    assert second["b.py"].deletions == 0


def test_every_touch_points_at_a_real_commit(repo: Path) -> None:
    commits, touches = walk_history(repo)
    known = {c.sha for c in commits}
    assert touches
    assert all(t.sha in known for t in touches)


def test_max_commits_bounds_the_walk(repo: Path) -> None:
    commits, _ = walk_history(repo, max_commits=1)
    assert len(commits) == 1
    assert commits[0].subject == "second: touch both"


def test_merge_is_flagged(tmp_path: Path) -> None:
    root = tmp_path / "merged"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "ada@example.com")
    _git(root, "config", "user.name", "Ada")
    (root / "a.py").write_text("x = 1\n")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "base")
    _git(root, "checkout", "-q", "-b", "side")
    (root / "side.py").write_text("s = 1\n")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "side work")
    _git(root, "checkout", "-q", "main")
    (root / "main.py").write_text("m = 1\n")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "main work")
    _git(root, "merge", "--no-ff", "-q", "side", "-m", "Merge side")

    commits, _ = walk_history(root)
    by_subject = {c.subject: c for c in commits}
    assert by_subject["Merge side"].is_merge is True
    assert by_subject["main work"].is_merge is False


def test_a_directory_that_is_not_a_repo_returns_empty(tmp_path: Path) -> None:
    """History is an enrichment; it never fails an ingest (§20.1)."""
    assert walk_history(tmp_path) == ([], [])


def test_single_commit_repo_still_yields_it(tmp_path: Path) -> None:
    root = tmp_path / "one"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "a@b.c")
    _git(root, "config", "user.name", "A")
    (root / "only.py").write_text("v = 1\n")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "just the one")

    commits, touches = walk_history(root)
    assert len(commits) == 1
    assert [t.file_path for t in touches] == ["only.py"]


# --- rename notation -------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("src/{old => new}.py", "src/new.py"),
        ("old.py => new.py", "new.py"),
        ("plain/path.py", "plain/path.py"),
        # A file moving up a level leaves an empty brace half.
        ("pkg/{sub/ => }mod.py", "pkg/mod.py"),
        ("{a => b}/mod.py", "b/mod.py"),
    ],
)
def test_normalise_path_resolves_renames(raw: str, expected: str) -> None:
    """git reports the rename, not the destination; §20 stores the destination."""
    assert normalise_path(raw) == expected


def test_rename_is_recorded_under_its_new_path(tmp_path: Path) -> None:
    root = tmp_path / "renamed"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "a@b.c")
    _git(root, "config", "user.name", "A")
    (root / "before.py").write_text("value = 1\n" * 20)
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "add before")
    _git(root, "mv", "before.py", "after.py")
    _git(root, "commit", "-q", "-m", "rename it")

    _, touches = walk_history(root)
    renamed = [t for t in touches if t.sha == walk_history(root)[0][0].sha]
    assert [t.file_path for t in renamed] == ["after.py"]
