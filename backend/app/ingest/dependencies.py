"""Third-party dependencies: what this repo declares, and where it uses them.

FEATURE-IDEAS 2.5. Two halves that only mean something together:

* **Declared** — parsed from ``pyproject.toml`` and ``requirements*.txt``. What
  the project says it needs.
* **Used** — every ``import`` site in the corpus, classified against the
  standard library and the repo's own top-level packages. What the code
  actually reaches for.

The gap between them is the point. A package declared and never imported is
probably dead weight; a package imported and never declared works on the
author's machine and fails on a fresh clone. Neither is visible from either
half alone.

**Imports come from the AST, not from Jedi, and that is the load-bearing
choice.** ``§6.1``'s resolver answers "does this name point at a symbol in this
repo", and for anything outside the repo it correctly stops caring — an import
resolved into site-packages is dropped, and one that resolves nowhere because
the package is not installed is counted as a failure. Measured on flask, 154 of
205 import failures were ``from werkzeug …``, a package absent from the ingest
environment. Reading the import statement instead makes this exact and
environment-independent: ``import werkzeug`` names ``werkzeug`` whether or not
werkzeug is installed, on any machine, forever.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from tree_sitter import Node

from app.config import IGNORE_DIRS
from app.ingest.filters import is_test_path

logger = logging.getLogger(__name__)

STDLIB = frozenset(sys.stdlib_module_names)

# Modules that exist only inside a type checker and can never be installed.
# `from _typeshed.wsgi import WSGIEnvironment` appears 8 times in flask, under
# `if TYPE_CHECKING:`, and reporting it as an undeclared dependency sends a
# reader looking for a package that does not exist on PyPI.
#
# Deliberately narrow. The broader rule — "skip every import under
# TYPE_CHECKING" — is wrong: `typing_extensions` is imported exactly that way
# and *is* a real dependency that projects declare. Only the modules that
# cannot be installed at all belong here.
TYPING_ONLY = frozenset({"_typeshed"})

KIND_STDLIB = "stdlib"
KIND_FIRST_PARTY = "first_party"
KIND_THIRD_PARTY = "third_party"

# Packages whose *import name* differs from the name you install, mapped
# module -> distribution.
#
# Without this the two halves of §26 disagree with themselves: flask declares
# `python-dotenv` and imports `dotenv`, so the same package appeared as
# undeclared *and* as unused — one package producing two contradictory
# findings, which is worse than not reporting it.
#
# The general solution is `importlib.metadata`'s `top_level.txt`, and it is
# rejected on purpose: it requires the package to be *installed*, which is the
# environment dependence this whole module exists to avoid. So this is a
# lookup table, and a lookup table is never complete — it covers the
# mismatches common enough to have bitten someone. An unlisted mismatch
# degrades exactly as before, which is why `declared=False` is documented as
# "no manifest row under this name" rather than "undeclared".
MODULE_TO_DISTRIBUTION: dict[str, str] = {
    "attr": "attrs",
    "bs4": "beautifulsoup4",
    "cv2": "opencv-python",
    "dateutil": "python-dateutil",
    "dotenv": "python-dotenv",
    "jwt": "pyjwt",
    "OpenSSL": "pyopenssl",
    "PIL": "pillow",
    "pkg_resources": "setuptools",
    "serial": "pyserial",
    "sklearn": "scikit-learn",
    "yaml": "pyyaml",
    "zoneinfo": "backports-zoneinfo",
}


def distribution_for(module: str) -> str:
    """The normalised distribution name a module most likely comes from."""
    return normalize(MODULE_TO_DISTRIBUTION.get(module, module))

# Manifests read from the clone. `filters.py` selects `*.py` only, so none of
# these are in the `files` table and none can be read back later — they have to
# be parsed inside the clone context, next to the symbol pass.
PYPROJECT = "pyproject.toml"
REQUIREMENTS_GLOB = "requirements*.txt"
# Where a project conventionally hides its requirements files.
REQUIREMENTS_DIRS = ("", "requirements", "reqs")


@dataclass(frozen=True)
class ImportSite:
    """One `import` statement, reduced to the package it reaches for."""

    module: str  # top-level only: `werkzeug.security` -> `werkzeug`
    dotted: str  # as written, minus any alias
    kind: str  # stdlib | first_party | third_party
    file_path: str
    line: int
    is_test: bool


@dataclass(frozen=True)
class Declared:
    """One dependency as the project declares it."""

    name: str  # normalised: `Werkzeug>=3.0` -> `werkzeug`
    raw: str  # the requirement as written
    source: str  # `pyproject.toml`, `requirements-dev.txt`, …
    extra: str | None  # optional-dependency group, when it came from one


def normalize(name: str) -> str:
    """PEP 503 normalisation: the form two spellings of one package agree on.

    `Flask-SQLAlchemy`, `flask_sqlalchemy` and `flask.sqlalchemy` are the same
    distribution, and matching declared against imported fails on that alone
    if the names are compared as written.
    """
    return re.sub(r"[-_.]+", "-", name.strip()).lower()


# ---------------------------------------------------------------------------
# Used: imports, straight from the AST
# ---------------------------------------------------------------------------


def _top_level(dotted: str) -> str:
    return dotted.split(".", 1)[0]


def _dotted_of(node: Node) -> str | None:
    """The dotted path of an import target, unwrapping `x as y`."""
    if node.type == "aliased_import":
        inner = node.named_children[0] if node.named_children else None
        return None if inner is None else _dotted_of(inner)
    if node.type == "dotted_name":
        # `Node.text` is None when the source buffer is gone; not reachable on
        # a tree we just parsed, but it is the typed contract.
        return None if node.text is None else node.text.decode("utf-8", "replace")
    return None


def extract_imports(
    root: Node, file_path: str, first_party: frozenset[str]
) -> list[ImportSite]:
    """Every non-relative import in one file, classified.

    Relative imports (``from . import x``) are skipped rather than recorded as
    first-party: they name no package, so they would add rows that can never
    match a declared dependency and cannot be counted as usage of anything.

    ``from __future__ import annotations`` parses as its own node type
    (``future_import_statement``) and is therefore never seen here. That is the
    right outcome — ``__future__`` is stdlib — and it is stated because the
    silence is otherwise indistinguishable from a bug.
    """
    sites: list[ImportSite] = []
    is_test = is_test_path(file_path)
    stack: list[Node] = [root]
    while stack:
        node = stack.pop()
        dotted_names: list[str] = []

        if node.type == "import_statement":
            # `import a.b, c` — every named child is a target.
            for child in node.named_children:
                dotted = _dotted_of(child)
                if dotted:
                    dotted_names.append(dotted)
        elif node.type == "import_from_statement":
            module = node.child_by_field_name("module_name")
            # `relative_import` is `.`, `..pkg` — in-repo by construction.
            if module is not None and module.type != "relative_import":
                dotted = _dotted_of(module)
                if dotted:
                    dotted_names.append(dotted)

        for dotted in dotted_names:
            top = _top_level(dotted)
            if not top:
                continue
            sites.append(
                ImportSite(
                    module=top,
                    dotted=dotted,
                    kind=classify(top, first_party),
                    file_path=file_path,
                    line=node.start_point[0] + 1,
                    is_test=is_test,
                )
            )
        stack.extend(node.named_children)
    return sites


def classify(module: str, first_party: frozenset[str]) -> str:
    """stdlib / first-party / third-party, in that precedence.

    stdlib wins over first-party deliberately: a repo with its own `types.py`
    at the root shadows the standard library for its own imports, but calling
    that a *dependency* of the project would be wrong in the only sense this
    module cares about — nothing needs installing for it.
    """
    if module in STDLIB or module in TYPING_ONLY:
        return KIND_STDLIB
    if module in first_party:
        return KIND_FIRST_PARTY
    return KIND_THIRD_PARTY


def first_party_names(repo_dir: Path, import_roots: list[str]) -> frozenset[str]:
    """Module names this repo can satisfy from its own tree.

    Two sources, because a depth-1 scan is demonstrably not enough:

    * **Modules directly under an import root** — the same roots the symbol
      pass puts on Jedi's path (``symbols.import_roots``), so a `src/` layout
      classifies its own package as first-party rather than reporting `flask`
      as a third-party dependency of flask.

    * **Package roots anywhere in the tree** — any directory holding
      ``__init__.py`` whose *parent* is not itself a package. That is precisely
      the definition of an importable top-level package, and it is what a depth-1
      scan misses: flask keeps fixture applications at
      ``tests/test_apps/blueprintapp/``, imported by the suite after a
      ``sys.path`` insert. Without this they read as third-party packages that
      the project forgot to declare — five of them, all wrong.

    A vendored copy of a real third-party package is classified first-party by
    this rule, which is the correct answer: it ships with the repo and needs no
    installing.
    """
    names: set[str] = set()

    for root in [repo_dir, *(Path(r) for r in import_roots)]:
        try:
            children = list(root.iterdir())
        except OSError:
            continue
        for child in children:
            if child.is_dir() and (child / "__init__.py").is_file():
                names.add(child.name)
            elif child.suffix == ".py" and child.stem != "__init__":
                names.add(child.stem)

    for dirpath, dirnames, filenames in os.walk(repo_dir):
        # Prune in place so the walk never descends into a vendor tree.
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS]
        if "__init__.py" not in filenames:
            continue
        here = Path(dirpath)
        if here == repo_dir:
            continue
        # A package inside a package is not importable by its bare name.
        if (here.parent / "__init__.py").is_file():
            continue
        names.add(here.name)

    return frozenset(names)


# ---------------------------------------------------------------------------
# Declared: manifests
# ---------------------------------------------------------------------------

# A requirement line, reduced to its distribution name. Stops at the first
# character that can begin a version specifier, an extra, a marker, or a
# comment. Deliberately not a PEP 508 parser: `packaging` is a dependency
# (CLAUDE.md rule 11) and the name is the only field anything here uses.
_REQ_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def parse_requirement(line: str) -> str | None:
    """The distribution name from one requirement line, or None.

    Skipped: blank lines, comments, flags (`-r other.txt`, `-e .`, `--index-url`)
    and anything URL-shaped, none of which name a package in a form worth
    guessing at.
    """
    text = line.split("#", 1)[0].strip()
    if not text or text.startswith("-"):
        return None
    if "://" in text or text.startswith("."):
        return None
    match = _REQ_NAME.match(text)
    return match.group(1) if match else None


def _requirements_files(repo_dir: Path) -> list[Path]:
    found: list[Path] = []
    for sub in REQUIREMENTS_DIRS:
        base = repo_dir / sub if sub else repo_dir
        if not base.is_dir():
            continue
        try:
            found.extend(sorted(base.glob(REQUIREMENTS_GLOB)))
        except OSError:
            continue
    return found


def _from_pyproject(repo_dir: Path) -> list[Declared]:
    path = repo_dir / PYPROJECT
    if not path.is_file():
        return []
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (tomllib.TOMLDecodeError, OSError) as exc:
        # A manifest we cannot read is a missing manifest, not a failed ingest.
        logger.warning("could not parse %s: %s", PYPROJECT, exc)
        return []

    out: list[Declared] = []
    project = data.get("project")
    if not isinstance(project, dict):
        return out
    for raw in project.get("dependencies") or []:
        if isinstance(raw, str) and (name := parse_requirement(raw)):
            out.append(Declared(normalize(name), raw, PYPROJECT, None))
    optional = project.get("optional-dependencies")
    if isinstance(optional, dict):
        for extra, reqs in optional.items():
            for raw in reqs or []:
                if isinstance(raw, str) and (name := parse_requirement(raw)):
                    out.append(Declared(normalize(name), raw, PYPROJECT, str(extra)))
    return out


def parse_manifests(repo_dir: Path) -> list[Declared]:
    """Declared dependencies from every manifest this repo ships.

    Deduplicated on (name, source, extra) so a package pinned twice in one file
    is one row, while the same package in `pyproject.toml` *and*
    `requirements-dev.txt` stays two — which file asks for it is part of the
    answer.
    """
    found = _from_pyproject(repo_dir)
    for req in _requirements_files(repo_dir):
        try:
            lines = req.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        source = req.relative_to(repo_dir).as_posix()
        for line in lines:
            if name := parse_requirement(line):
                found.append(Declared(normalize(name), line.strip(), source, None))

    seen: set[tuple[str, str, str | None]] = set()
    unique: list[Declared] = []
    for d in found:
        key = (d.name, d.source, d.extra)
        if key not in seen:
            seen.add(key)
            unique.append(d)
    return unique
