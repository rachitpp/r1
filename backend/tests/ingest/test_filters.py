"""Filter tests: SPEC §2.2 selection rules against a real git index."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import MAX_FILE_BYTES
from app.exceptions import TooManyFilesError
from app.ingest.filters import is_test_path, select_files


def _kept_paths(repo_dir: Path) -> set[str]:
    return {f.path for f in select_files(repo_dir).files}


def test_keeps_code_and_prose_and_drops_the_rest(make_repo) -> None:
    """§30.2 widened step 2: `*.py` **or** a prose/config path, nothing else."""
    repo = make_repo(
        {
            "a.py": "x = 1\n",
            "README.md": "# hi\n",
            "notes.txt": "no\n",
            "logo.svg": "<svg/>\n",
            "data.csv": "a,b\n",
        }
    )
    result = select_files(repo)
    assert {f.path for f in result.files} == {"a.py", "README.md", "notes.txt"}
    assert result.skipped_unsupported == 2  # the svg and the csv


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


# --- is_test classification (SPEC §2.6) -----------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "tests/test_auth.py",
        "tests/models/test_responses.py",
        "test/helpers.py",
        "testing/fixtures.py",
        "httpx/tests/thing.py",  # segment at any depth
        "test_client.py",  # filename rule, no test dir
        "httpx/_client_test.py",
        "conftest.py",
        "httpx/conftest.py",
    ],
)
def test_is_test_path_flags_test_code(path: str) -> None:
    assert is_test_path(path) is True


@pytest.mark.parametrize(
    "path",
    [
        "httpx/_auth.py",
        "httpx/_decoders.py",
        "httpx/__init__.py",
        "src/latest/protest.py",  # substring, not a segment
        "httpx/contest.py",  # not conftest.py
        "httpx/attest.py",  # does not start with test_
    ],
)
def test_is_test_path_leaves_implementation_alone(path: str) -> None:
    assert is_test_path(path) is False


def test_is_test_path_ignores_a_test_named_directory_as_final_component() -> None:
    """Segment matching looks at directories only, never the filename itself."""
    assert is_test_path("httpx/test.py") is False


def test_selection_still_includes_test_files(make_repo) -> None:
    """Classification must not become exclusion — tests stay in the corpus."""
    repo = make_repo({"httpx/_auth.py": "x = 1\n", "tests/test_auth.py": "y = 2\n"})
    kept = {f.path for f in select_files(repo).files}
    assert kept == {"httpx/_auth.py", "tests/test_auth.py"}
