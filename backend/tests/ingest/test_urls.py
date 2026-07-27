"""GitHub URL normalization (SPEC §8, ``POST /repos`` 422 path)."""

from __future__ import annotations

import pytest

from app.exceptions import InvalidRepoUrlError
from app.ingest.urls import normalize_github_url

CANONICAL = "https://github.com/encode/httpx"


@pytest.mark.parametrize(
    "raw",
    [
        "https://github.com/encode/httpx",
        "https://github.com/encode/httpx/",
        "https://github.com/encode/httpx.git",
        "http://github.com/encode/httpx",
        "https://www.github.com/encode/httpx",
        "github.com/encode/httpx",
        "  https://github.com/encode/httpx  ",
        # Deep links are what people actually paste out of the browser.
        "https://github.com/encode/httpx/tree/master/httpx",
        "https://github.com/encode/httpx/blob/master/httpx/_client.py#L120",
    ],
)
def test_variants_reduce_to_one_canonical_url(raw: str) -> None:
    """`repos.url` is UNIQUE — every spelling must land on the same row."""
    url, name = normalize_github_url(raw)
    assert url == CANONICAL
    assert name == "encode/httpx"


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "not a url",
        "https://gitlab.com/owner/repo",
        "https://example.com/encode/httpx",
        "https://github.com/encode",  # owner only, not a repo
        "https://github.com/",
        "git@github.com:encode/httpx.git",  # ssh: v1 is public repos over https
        "ftp://github.com/encode/httpx",
    ],
)
def test_rejects_non_github_repo_urls(raw: str) -> None:
    with pytest.raises(InvalidRepoUrlError):
        normalize_github_url(raw)
