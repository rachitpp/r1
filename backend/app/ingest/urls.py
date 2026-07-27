"""GitHub URL validation and normalization (SPEC §8, ``POST /repos``).

``repos.url`` is UNIQUE (§3), so the same repository submitted as
``github.com/Owner/Repo``, ``.../repo.git``, or a deep link into the file tree
must reduce to one canonical string — otherwise "submit the repo you already
indexed" silently re-ingests it under a second row.

Pure functions, no I/O: whether the repo actually exists is the clone's problem
(``CloneError``), not this module's. v1 is public repos only, so nothing here
handles credentials.
"""

from __future__ import annotations

from urllib.parse import urlparse

from app.exceptions import InvalidRepoUrlError

ALLOWED_HOSTS = frozenset({"github.com", "www.github.com"})

# Path segments GitHub uses for views of a repo rather than the repo itself.
# A pasted "…/blob/main/httpx/_client.py" is a normal thing for a human to do;
# the repo is still the first two segments, so we trim rather than reject.
_VIEW_SEGMENTS = frozenset(
    {"tree", "blob", "commits", "commit", "pull", "issues", "releases", "actions"}
)


def normalize_github_url(raw: str) -> tuple[str, str]:
    """Return ``(canonical_url, "owner/repo")`` for ``raw``.

    Accepts with or without scheme, with or without ``.git``, and deep links
    into a repo's views. Raises :class:`InvalidRepoUrlError` for anything that
    is not a public GitHub repository URL.
    """
    text = (raw or "").strip()
    if not text:
        raise InvalidRepoUrlError("url must not be empty")

    # A bare "github.com/owner/repo" parses as a path with no host; give the
    # parser the scheme it needs rather than making the user type it.
    if "://" not in text:
        text = f"https://{text}"

    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"}:
        raise InvalidRepoUrlError(
            f"unsupported scheme {parsed.scheme!r}; use an https GitHub URL"
        )
    host = parsed.netloc.lower()
    if host not in ALLOWED_HOSTS:
        raise InvalidRepoUrlError(
            f"{host or raw!r} is not github.com; v1 indexes public GitHub repos only"
        )

    segments = [s for s in parsed.path.split("/") if s]
    if len(segments) >= 3 and segments[2] in _VIEW_SEGMENTS:
        segments = segments[:2]
    if len(segments) != 2:
        raise InvalidRepoUrlError(
            "expected a repository URL of the form https://github.com/owner/repo"
        )

    owner, repo = segments
    if repo.endswith(".git"):
        repo = repo[: -len(".git")]
    if not owner or not repo:
        raise InvalidRepoUrlError(
            "expected a repository URL of the form https://github.com/owner/repo"
        )

    name = f"{owner}/{repo}"
    return f"https://github.com/{name}", name
