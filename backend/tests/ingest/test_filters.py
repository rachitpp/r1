"""Filter tests: SPEC §2.2 selection rules against a real git index."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import MAX_FILE_BYTES
from app.exceptions import TooManyFilesError
from app.ingest.filters import select_files


def _kept_paths(repo_dir: Path) -> set[str]:
    return {f.path for f in select_files(repo_dir).files}


def test_keeps_only_python(make_repo) -> None:
    repo = make_repo({"a.py": "x = 1\n", "README.md": "# hi\n", "b.txt": "no\n"})
    result = select_files(repo)
    assert {f.path for f in result.files} == {"a.py"}
    assert result.skipped_non_python == 2


def test_drops_ignore_dir_segment_at_any_depth(make_repo) -> None:
    repo = make_repo(
        {
            "app.py": "x = 1\n",
            ".venv/lib/pkg.py": "y = 2\n",
            "src/__pycache__/c.py": "z = 3\n",
        }
    )
    result = select_files(repo)
    assert _kept_paths(repo) == {"app.py"}
    assert result.skipped_ignored_dir == 2


def test_drops_files_over_size_cap(make_repo) -> None:
    big = "# " + "a" * (MAX_FILE_BYTES + 10)
    repo = make_repo({"small.py": "x = 1\n", "huge.py": big})
    result = select_files(repo)
    assert _kept_paths(repo) == {"small.py"}
    assert result.skipped_too_large == 1


def test_binary_sniff_drops_null_byte_files(make_repo) -> None:
    repo = make_repo({"ok.py": "x = 1\n", "bin.py": b"\x00\x01\x02 code\n"})
    result = select_files(repo)
    assert _kept_paths(repo) == {"ok.py"}
    assert result.skipped_binary == 1


def test_non_utf8_is_skipped_with_count(make_repo) -> None:
    # 0xff 0xfe is invalid UTF-8 but contains no null byte.
    repo = make_repo({"ok.py": "x = 1\n", "latin.py": b"\xff\xfe = 1\n"})
    result = select_files(repo)
    assert _kept_paths(repo) == {"ok.py"}
    assert result.skipped_decode_error == 1


def test_source_file_metadata(make_repo) -> None:
    repo = make_repo({"a.py": "line1\nline2\nline3\n"})
    (source,) = select_files(repo).files
    assert source.path == "a.py"
    assert source.n_lines == 3
    assert source.text == "line1\nline2\nline3\n"


def test_max_files_guard(make_repo, monkeypatch) -> None:
    monkeypatch.setattr("app.ingest.filters.MAX_FILES", 1)
    repo = make_repo({"a.py": "x = 1\n", "b.py": "y = 2\n"})
    with pytest.raises(TooManyFilesError):
        select_files(repo)
