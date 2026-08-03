"""§26 dependency views: what the repo stands on, and what it declares.

Reads over rows the ingest pass wrote — no model, no tool budget. What these
pin is the shape of the *answer*: third-party only, tests filtered by default,
the alias reconciliation that stops one package being reported twice, and the
"was this even indexed" distinction that keeps an empty list honest.
"""

from __future__ import annotations

import httpx

from tests.api.conftest import (
    FILE_PATH,
    INDEXING_REPO_ID,
    REPO_ID,
    UNKNOWN_REPO_ID,
)


async def test_lists_third_party_packages_most_used_first(
    client: httpx.AsyncClient,
) -> None:
    body = (await client.get(f"/repos/{REPO_ID}/dependencies")).json()
    assert body["indexed"] is True
    assert [p["module"] for p in body["packages"]][0] == "werkzeug"
    assert body["packages"][0]["n_uses"] == 2
    assert body["packages"][0]["n_files"] == 1


async def test_stdlib_is_not_a_dependency(client: httpx.AsyncClient) -> None:
    """`os` is stored as a use, but nothing installs it."""
    body = (await client.get(f"/repos/{REPO_ID}/dependencies")).json()
    assert "os" not in [p["module"] for p in body["packages"]]


async def test_test_only_imports_are_excluded_by_default(
    client: httpx.AsyncClient,
) -> None:
    body = (await client.get(f"/repos/{REPO_ID}/dependencies")).json()
    assert "pytest" not in [p["module"] for p in body["packages"]]

    body = (
        await client.get(f"/repos/{REPO_ID}/dependencies?include_tests=true")
    ).json()
    assert "pytest" in [p["module"] for p in body["packages"]]


async def test_declared_package_is_marked_declared(
    client: httpx.AsyncClient,
) -> None:
    body = (await client.get(f"/repos/{REPO_ID}/dependencies")).json()
    werkzeug = next(p for p in body["packages"] if p["module"] == "werkzeug")
    assert werkzeug["declared"] is True
    assert werkzeug["requirement"] == "werkzeug>=3.0"
    assert werkzeug["sources"] == ["pyproject.toml"]


async def test_imported_but_never_declared_is_reported(
    client: httpx.AsyncClient,
) -> None:
    body = (await client.get(f"/repos/{REPO_ID}/dependencies")).json()
    assert "requests" in body["undeclared"]


async def test_declared_but_never_imported_is_reported(
    client: httpx.AsyncClient,
) -> None:
    body = (await client.get(f"/repos/{REPO_ID}/dependencies")).json()
    assert [u["name"] for u in body["unused"]] == ["abandoned"]
    assert body["unused"][0]["requirement"] == "abandoned==1.0"


async def test_a_test_only_dependency_is_not_called_unused(
    client: httpx.AsyncClient,
) -> None:
    """`pytest` is imported only by tests, and that is still *used*.

    Filtering tests out of `unused` too would report every test dependency as
    dead weight — plausible, and wrong.
    """
    body = (await client.get(f"/repos/{REPO_ID}/dependencies")).json()
    assert "pytest" not in [u["name"] for u in body["unused"]]


async def test_alias_stops_one_package_being_reported_twice(
    client: httpx.AsyncClient,
) -> None:
    """The flask case: declares `python-dotenv`, imports `dotenv`.

    Without reconciliation the same package appears in `undeclared` *and* in
    `unused` — two contradictory findings about one dependency.
    """
    body = (await client.get(f"/repos/{REPO_ID}/dependencies")).json()
    dotenv = next(p for p in body["packages"] if p["module"] == "dotenv")
    assert dotenv["declared"] is True
    assert "dotenv" not in body["undeclared"]
    assert "python-dotenv" not in [u["name"] for u in body["unused"]]


async def test_alias_matched_package_carries_its_manifest(
    client: httpx.AsyncClient,
) -> None:
    """"declared" beside an empty requirement reads as a bug in the panel."""
    body = (await client.get(f"/repos/{REPO_ID}/dependencies")).json()
    dotenv = next(p for p in body["packages"] if p["module"] == "dotenv")
    assert dotenv["requirement"] == "python-dotenv"
    assert dotenv["sources"] == ["pyproject.toml"]


async def test_snapshot_ingested_before_the_pass_says_so(
    client: httpx.AsyncClient,
) -> None:
    """§26.3: an empty list must not read as "this project has no dependencies"."""
    body = (await client.get(f"/repos/{INDEXING_REPO_ID}/dependencies")).json()
    assert body["indexed"] is False
    assert body["packages"] == []


async def test_uses_lists_every_import_site(client: httpx.AsyncClient) -> None:
    body = (await client.get(f"/repos/{REPO_ID}/dependencies/werkzeug")).json()
    assert body["module"] == "werkzeug"
    assert [u["start_line"] for u in body["uses"]] == [3, 4]
    assert body["uses"][0]["file_path"] == FILE_PATH
    assert body["uses"][0]["dotted"] == "werkzeug.security"


async def test_uses_excludes_tests_by_default(client: httpx.AsyncClient) -> None:
    body = (await client.get(f"/repos/{REPO_ID}/dependencies/pytest")).json()
    assert body["uses"] == []

    body = (
        await client.get(f"/repos/{REPO_ID}/dependencies/pytest?include_tests=true")
    ).json()
    assert len(body["uses"]) == 1


async def test_unknown_module_is_empty_not_404(client: httpx.AsyncClient) -> None:
    """"Where is X used" and "X is not used" are the same answer (§18.3)."""
    resp = await client.get(f"/repos/{REPO_ID}/dependencies/nosuchpackage")
    assert resp.status_code == 200
    assert resp.json()["uses"] == []


# --- tenancy (§13.5) -------------------------------------------------------


async def test_unknown_repo_is_404(client: httpx.AsyncClient) -> None:
    resp = await client.get(f"/repos/{UNKNOWN_REPO_ID}/dependencies")
    assert resp.status_code == 404


async def test_another_tenants_repo_is_404(other_client: httpx.AsyncClient) -> None:
    """Not 403: that would confirm the id names a real repo."""
    assert (await other_client.get(f"/repos/{REPO_ID}/dependencies")).status_code == 404
    assert (
        await other_client.get(f"/repos/{REPO_ID}/dependencies/werkzeug")
    ).status_code == 404


async def test_anonymous_is_rejected(anon_client: httpx.AsyncClient) -> None:
    assert (await anon_client.get(f"/repos/{REPO_ID}/dependencies")).status_code == 401
