"""Clone-helper unit tests (no network): URL parsing and Windows cleanup."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from app.ingest.clone import _rmtree, repo_name_from_url


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://github.com/encode/httpx", "encode/httpx"),
        ("https://github.com/encode/httpx.git", "encode/httpx"),
        ("https://github.com/encode/httpx/", "encode/httpx"),
        ("git@github.com:pallets/flask.git", "pallets/flask"),
    ],
)
def test_repo_name_from_url(url: str, expected: str) -> None:
    assert repo_name_from_url(url) == expected


def test_rmtree_removes_readonly_files(tmp_path: Path) -> None:
    # Mimic git's read-only packed objects: rmtree must clear the bit and delete.
    work = tmp_path / "clone"
    work.mkdir()
    locked = work / "objects" / "pack"
    locked.mkdir(parents=True)
    obj = locked / "readonly.idx"
    obj.write_text("data")
    os.chmod(obj, stat.S_IREAD)

    _rmtree(work)
    assert not work.exists()


def test_rmtree_missing_path_is_noop(tmp_path: Path) -> None:
    _rmtree(tmp_path / "does-not-exist")  # must not raise
