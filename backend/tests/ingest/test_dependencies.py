"""Dependency extraction: manifests, imports, and the classification between.

The imports half runs against real tree-sitter output rather than a mock —
the node shapes (`import_statement` vs `import_from_statement` vs
`future_import_statement`) are the thing under test, and asserting against a
hand-built tree would test the fixture.
"""

from __future__ import annotations

import pytest

from app.ingest.dependencies import (
    KIND_FIRST_PARTY,
    KIND_STDLIB,
    KIND_THIRD_PARTY,
    classify,
    distribution_for,
    extract_imports,
    first_party_names,
    normalize,
    parse_manifests,
    parse_requirement,
)
from app.ingest.parser import parse_tree

FIRST_PARTY = frozenset({"mypkg"})


def imports_of(source: str, path: str = "mypkg/mod.py"):
    tree = parse_tree(source)
    assert tree is not None
    return extract_imports(tree.root_node, path, FIRST_PARTY)


# --- import extraction -----------------------------------------------------


def test_plain_import_records_top_level_package() -> None:
    (site,) = imports_of("import werkzeug\n")
    assert (site.module, site.dotted, site.kind) == (
        "werkzeug",
        "werkzeug",
        KIND_THIRD_PARTY,
    )
    assert site.line == 1


def test_dotted_import_records_only_the_installable_unit() -> None:
    """`werkzeug.security` is not a package you can declare; `werkzeug` is."""
    (site,) = imports_of("import werkzeug.security\n")
    assert site.module == "werkzeug"
    assert site.dotted == "werkzeug.security"


def test_from_import_records_the_module_not_the_name() -> None:
    (site,) = imports_of("from werkzeug.security import check_password_hash\n")
    assert site.module == "werkzeug"
    assert site.dotted == "werkzeug.security"


def test_aliased_import_unwraps_the_alias() -> None:
    (site,) = imports_of("import numpy as np\n")
    assert (site.module, site.dotted) == ("numpy", "numpy")


def test_one_statement_importing_several_packages_yields_several_sites() -> None:
    sites = imports_of("import os, werkzeug, numpy\n")
    assert [s.module for s in sites] == ["os", "werkzeug", "numpy"]


@pytest.mark.parametrize(
    "source",
    [
        "from . import sibling\n",
        "from .relative import thing\n",
        "from ..pkg import other\n",
    ],
)
def test_relative_imports_are_skipped(source: str) -> None:
    """They name no package, so they can never match a declared dependency."""
    assert imports_of(source) == []


def test_future_import_is_not_recorded() -> None:
    """It parses as `future_import_statement`, so it never reaches us.

    Asserted rather than assumed: the silence is otherwise indistinguishable
    from the extractor quietly missing a node type.
    """
    assert imports_of("from __future__ import annotations\n") == []


def test_import_inside_a_function_is_found() -> None:
    """Optional dependencies are usually imported lazily, inside a function."""
    sites = imports_of(
        "def render():\n"
        "    import jinja2\n"
        "    return jinja2\n"
    )
    assert [s.module for s in sites] == ["jinja2"]
    assert sites[0].line == 2


def test_classification_splits_the_three_buckets() -> None:
    sites = imports_of("import os\nimport mypkg\nimport werkzeug\n")
    assert {s.module: s.kind for s in sites} == {
        "os": KIND_STDLIB,
        "mypkg": KIND_FIRST_PARTY,
        "werkzeug": KIND_THIRD_PARTY,
    }


def test_is_test_follows_the_corpus_wide_rule() -> None:
    (site,) = imports_of("import pytest\n", path="tests/test_thing.py")
    assert site.is_test is True


def test_stdlib_wins_over_a_shadowing_first_party_module() -> None:
    """A repo with its own `types.py` still needs nothing installed for it."""
    assert classify("types", frozenset({"types"})) == KIND_STDLIB


# --- first-party detection -------------------------------------------------


def test_first_party_names_sees_a_src_layout_package(make_repo) -> None:
    repo = make_repo(
        {
            "src/widget/__init__.py": "",
            "src/widget/core.py": "x = 1\n",
            "setup.py": "pass\n",
        }
    )
    names = first_party_names(repo, [str(repo / "src")])
    # `widget` via the src root, `setup` as a top-level module.
    assert "widget" in names
    assert "setup" in names


def test_flask_does_not_depend_on_flask(make_repo) -> None:
    """The regression this guards: a src-layout package read as third-party."""
    repo = make_repo({"src/flask/__init__.py": "", "src/flask/app.py": "x = 1\n"})
    names = first_party_names(repo, [str(repo / "src")])
    assert classify("flask", names) == KIND_FIRST_PARTY


# --- manifests -------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("werkzeug>=3.0", "werkzeug"),
        ("Flask-SQLAlchemy == 3.1.1", "Flask-SQLAlchemy"),
        ("requests[security]>=2", "requests"),
        ('importlib-metadata; python_version < "3.10"', "importlib-metadata"),
        ("  jinja2  # templating", "jinja2"),
        ("", None),
        ("# a comment", None),
        ("-r other-requirements.txt", None),
        ("-e .", None),
        ("--index-url https://example.test/simple", None),
        ("https://example.test/pkg.tar.gz", None),
        ("./local-package", None),
    ],
)
def test_parse_requirement(line: str, expected: str | None) -> None:
    assert parse_requirement(line) == expected


def test_normalize_collapses_pep503_spellings() -> None:
    assert normalize("Flask-SQLAlchemy") == "flask-sqlalchemy"
    assert normalize("flask_sqlalchemy") == "flask-sqlalchemy"
    assert normalize("flask.sqlalchemy") == "flask-sqlalchemy"


def test_parse_manifests_reads_pyproject_main_and_extras(make_repo) -> None:
    repo = make_repo(
        {
            "pyproject.toml": (
                "[project]\n"
                'name = "demo"\n'
                'dependencies = ["werkzeug>=3.0", "Jinja2"]\n'
                "\n"
                "[project.optional-dependencies]\n"
                'dev = ["pytest>=8"]\n'
            )
        }
    )
    found = {(d.name, d.extra) for d in parse_manifests(repo)}
    assert ("werkzeug", None) in found
    assert ("jinja2", None) in found
    # The group is kept: a `[dev]` package is not something a user installs.
    assert ("pytest", "dev") in found


def test_parse_manifests_reads_requirements_files(make_repo) -> None:
    repo = make_repo(
        {
            "requirements.txt": "werkzeug>=3.0\n# comment\n\n",
            "requirements-dev.txt": "pytest\n",
        }
    )
    found = {(d.name, d.source) for d in parse_manifests(repo)}
    assert ("werkzeug", "requirements.txt") in found
    assert ("pytest", "requirements-dev.txt") in found


def test_same_package_in_two_manifests_stays_two_rows(make_repo) -> None:
    """Which file wants it is part of the answer, so this is not deduplicated."""
    repo = make_repo(
        {
            "pyproject.toml": '[project]\nname = "d"\ndependencies = ["werkzeug"]\n',
            "requirements.txt": "werkzeug\n",
        }
    )
    sources = sorted(d.source for d in parse_manifests(repo) if d.name == "werkzeug")
    assert sources == ["pyproject.toml", "requirements.txt"]


def test_unparseable_pyproject_is_a_missing_manifest_not_a_failure(
    make_repo,
) -> None:
    repo = make_repo({"pyproject.toml": "this is not [ valid toml\n"})
    assert parse_manifests(repo) == []


def test_repo_with_no_manifests_yields_nothing(make_repo) -> None:
    assert parse_manifests(make_repo({"pkg/__init__.py": ""})) == []


def test_pyproject_without_a_project_table_is_tolerated(make_repo) -> None:
    """Poetry and setuptools-only projects keep dependencies elsewhere."""
    repo = make_repo({"pyproject.toml": '[build-system]\nrequires = ["setuptools"]\n'})
    assert parse_manifests(repo) == []


# --- distribution aliases --------------------------------------------------


def test_distribution_for_maps_a_known_mismatch() -> None:
    """The flask case: it declares `python-dotenv` and imports `dotenv`."""
    assert distribution_for("dotenv") == "python-dotenv"
    assert distribution_for("yaml") == "pyyaml"
    assert distribution_for("PIL") == "pillow"


def test_distribution_for_normalises_an_unmapped_module() -> None:
    """An unlisted package degrades to plain normalisation, not to None."""
    assert distribution_for("werkzeug") == "werkzeug"
    assert distribution_for("Flask_SQLAlchemy") == "flask-sqlalchemy"


def test_alias_makes_the_two_halves_agree() -> None:
    """Without this, one package is reported undeclared *and* unused."""
    declared = {"python-dotenv"}
    assert distribution_for("dotenv") in declared
