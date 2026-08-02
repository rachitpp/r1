"""Symbol-graph tests: definitions, and Jedi-resolved cross-file edges (SPEC §6).

The edge tests run real Jedi against a real on-disk fixture repo — resolution
is the whole point of the pass, so mocking it would test nothing. They are
slower than the rest of the unit suite but need no network and no models.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.ingest.filters import select_files
from app.ingest.parser import parse_file
from app.ingest.symbols import (
    KIND_CALLS,
    KIND_EXTENDS,
    KIND_IMPORTS,
    _import_roots,
    _SpanIndex,
    extract_edges,
    extract_symbols,
)

# A tiny package with one cross-file import, one cross-file call, and one
# cross-file inheritance — the three edge kinds SPEC §6.1 defines.
CROSS_FILE_REPO: dict[str, str | bytes] = {
    "pkg/__init__.py": "",
    "pkg/base.py": (
        "class Engine:\n"
        "    def start(self):\n"
        "        return 'vroom'\n"
        "\n"
        "\n"
        "def helper(x):\n"
        "    return x + 1\n"
    ),
    "pkg/impl.py": (
        "from pkg.base import Engine, helper\n"
        "\n"
        "\n"
        "class TurboEngine(Engine):\n"
        "    def boost(self):\n"
        "        return helper(41)\n"
    ),
}


def _symbols_and_edges(repo_dir: Path):
    selection = select_files(repo_dir)
    parsed = [p for p in (parse_file(s) for s in selection.files) if p is not None]
    symbols = extract_symbols(parsed)
    edges, stats = extract_edges(repo_dir, selection.files, symbols)
    return symbols, edges, stats


# --- definitions -----------------------------------------------------------


def test_extract_symbols_projects_chunks(make_repo) -> None:
    repo = make_repo(CROSS_FILE_REPO)
    symbols, _, _ = _symbols_and_edges(repo)
    quals = {s.qualname for s in symbols}
    assert "pkg.base.Engine" in quals
    assert "pkg.base.Engine.start" in quals
    assert "pkg.base.helper" in quals
    assert "pkg.impl.TurboEngine" in quals


def test_symbol_short_name_and_kind(make_repo) -> None:
    repo = make_repo(CROSS_FILE_REPO)
    symbols, _, _ = _symbols_and_edges(repo)
    by_qual = {s.qualname: s for s in symbols}
    assert by_qual["pkg.base.Engine.start"].name == "start"
    assert by_qual["pkg.base.Engine.start"].kind == "method"
    assert by_qual["pkg.base.Engine"].kind == "class"
    assert by_qual["pkg.base.helper"].kind == "function"


def test_symbols_carry_is_test_from_path(make_repo) -> None:
    """`is_test` mirrors the §2.6 chunk rule — extraction covers tests too."""
    repo = make_repo(
        {
            "pkg/__init__.py": "",
            "pkg/impl.py": "def real():\n    return 1\n",
            "tests/test_impl.py": "def test_real():\n    assert True\n",
        }
    )
    symbols, _, _ = _symbols_and_edges(repo)
    by_qual = {s.qualname: s for s in symbols}
    assert by_qual["pkg.impl.real"].is_test is False
    assert by_qual["tests.test_impl.test_real"].is_test is True


# --- span index ------------------------------------------------------------


def test_span_index_picks_innermost_symbol(make_repo) -> None:
    """A method's line resolves to the method, not its enclosing class."""
    repo = make_repo(CROSS_FILE_REPO)
    symbols, _, _ = _symbols_and_edges(repo)
    index = _SpanIndex(symbols)
    by_qual = {s.qualname: s for s in symbols}
    start = by_qual["pkg.base.Engine.start"]
    assert index.at("pkg/base.py", start.start_line) == (
        "pkg/base.py",
        start.start_line,
    )


def test_span_index_misses_outside_any_span(make_repo) -> None:
    repo = make_repo(CROSS_FILE_REPO)
    symbols, _, _ = _symbols_and_edges(repo)
    index = _SpanIndex(symbols)
    assert index.at("pkg/base.py", 9999) is None
    assert index.at("nonexistent.py", 1) is None


# --- edges (real Jedi resolution) ------------------------------------------


@pytest.mark.parametrize(
    ("kind", "from_qual", "to_qual"),
    [
        (KIND_EXTENDS, "pkg.impl.TurboEngine", "pkg.base.Engine"),
        (KIND_CALLS, "pkg.impl.TurboEngine.boost", "pkg.base.helper"),
    ],
)
def test_cross_file_edge_resolves(make_repo, kind, from_qual, to_qual) -> None:
    repo = make_repo(CROSS_FILE_REPO)
    symbols, edges, _ = _symbols_and_edges(repo)
    key_of = {s.qualname: (s.file_path, s.start_line) for s in symbols}
    found = {
        (e.from_key, e.to_key, e.kind) for e in edges
    }
    assert (key_of[from_qual], key_of[to_qual], kind) in found


def test_cross_file_import_edge_resolves(make_repo) -> None:
    """`from pkg.base import Engine` produces an imports edge into pkg.base."""
    repo = make_repo(CROSS_FILE_REPO)
    symbols, edges, _ = _symbols_and_edges(repo)
    by_key = {(s.file_path, s.start_line): s for s in symbols}
    imports = [e for e in edges if e.kind == KIND_IMPORTS]
    assert imports, "expected at least one imports edge"
    targets = {by_key[e.to_key].file_path for e in imports if e.to_key in by_key}
    assert "pkg/base.py" in targets


def test_edges_drop_targets_outside_repo(make_repo) -> None:
    """Stdlib and site-packages resolutions are dropped (SPEC §6.1)."""
    repo = make_repo(
        {
            "pkg/__init__.py": "",
            "pkg/uses_stdlib.py": (
                "import json\n"
                "\n"
                "\n"
                "def dump(x):\n"
                "    return json.dumps(x)\n"
            ),
        }
    )
    symbols, edges, _ = _symbols_and_edges(repo)
    files = {s.file_path for s in symbols}
    for e in edges:
        assert e.to_key[0] in files, f"edge escaped the repo: {e}"


def test_edge_stats_track_sites_and_resolution(make_repo) -> None:
    repo = make_repo(CROSS_FILE_REPO)
    _, edges, stats = _symbols_and_edges(repo)
    assert sum(stats.sites.values()) > 0
    assert stats.edges_written() == len(edges)
    assert 0.0 <= stats.unresolved_rate() <= 1.0
    assert stats.timed_out_files == []


def test_zero_timeout_budget_skips_edges(make_repo) -> None:
    """The per-file budget is checked between resolutions (SPEC §6.1)."""
    repo = make_repo(CROSS_FILE_REPO)
    selection = select_files(repo)
    parsed = [p for p in (parse_file(s) for s in selection.files) if p is not None]
    symbols = extract_symbols(parsed)
    edges, stats = extract_edges(repo, selection.files, symbols, timeout_s=-1)
    assert edges == []
    assert stats.timed_out_files, "expected files to report a blown budget"


def test_stats_separate_failures_from_external_drops(make_repo) -> None:
    """A stdlib call is an external drop, not a resolution failure.

    Guards the metric bug found on the first httpx run: counting external
    drops as "unresolved" reported 52% against SPEC §6.1's ~20% budget, when
    the real failure rate was 4%.
    """
    repo = make_repo(
        {
            "pkg/__init__.py": "",
            "pkg/uses_stdlib.py": (
                "import json\n"
                "\n"
                "\n"
                "def dump(x):\n"
                "    return json.dumps(x)\n"
            ),
        }
    )
    _, _, stats = _symbols_and_edges(repo)
    assert sum(stats.out_of_repo.values()) > 0, "stdlib target should be dropped"
    assert stats.failure_rate() < stats.unresolved_rate()


# --- name-agreement probe (SPEC §6.1 addendum) -----------------------------


def test_recursion_keeps_its_self_edge(make_repo) -> None:
    """Genuine recursion is a real call edge — names agree, so it survives.

    There is deliberately no self-edge ban: the name probe is what separates
    recursion from local-variable fabrication, so a blanket ban would throw
    away true edges to catch false ones.
    """
    repo = make_repo(
        {
            "pkg/__init__.py": "",
            "pkg/rec.py": (
                "def countdown(n):\n"
                "    if n <= 0:\n"
                "        return 0\n"
                "    return countdown(n - 1)\n"
            ),
        }
    )
    symbols, edges, _ = _symbols_and_edges(repo)
    key = next(
        (s.file_path, s.start_line) for s in symbols if s.qualname == "pkg.rec.countdown"
    )
    assert (key, key, KIND_CALLS) in {(e.from_key, e.to_key, e.kind) for e in edges}


def test_local_variable_call_does_not_fabricate_a_self_edge(make_repo) -> None:
    """Calling a local binding must not become an edge to the enclosing def.

    Innermost containment maps the resolved local to the function surrounding
    it; without the name probe that becomes a bogus self-edge. This is the
    pattern found on httpx (`digest`/`hash_func` in DigestAuth).
    """
    repo = make_repo(
        {
            "pkg/__init__.py": "",
            "pkg/local.py": (
                "def outer(flag):\n"
                "    handler = str if flag else repr\n"
                "    return handler(flag)\n"
            ),
        }
    )
    symbols, edges, stats = _symbols_and_edges(repo)
    key = next(
        (s.file_path, s.start_line) for s in symbols if s.qualname == "pkg.local.outer"
    )
    assert (key, key, KIND_CALLS) not in {
        (e.from_key, e.to_key, e.kind) for e in edges
    }
    assert sum(stats.stray.values()) > 0, "local-variable call should count as stray"


def test_module_import_exempt_from_name_probe(make_repo) -> None:
    """Importing a module-level binding yields an edge to the module.

    `TYPE_ALIAS` is not a chunked symbol, so containment maps it to the module
    — one candidate, nothing for the probe to discriminate. The edge is kept
    and counted under `module_import`, not `resolved` and not `stray`.
    """
    repo = make_repo(
        {
            "pkg/__init__.py": "",
            "pkg/types.py": "TYPE_ALIAS = int\n",
            "pkg/user.py": (
                "from pkg.types import TYPE_ALIAS\n"
                "\n"
                "\n"
                "def use(x: TYPE_ALIAS):\n"
                "    return x\n"
            ),
        }
    )
    symbols, edges, stats = _symbols_and_edges(repo)
    by_key = {(s.file_path, s.start_line): s for s in symbols}
    targets = {
        by_key[e.to_key].file_path for e in edges if e.kind == KIND_IMPORTS
    }
    assert "pkg/types.py" in targets
    assert sum(stats.module_import.values()) > 0
    assert stats.stray.get(KIND_IMPORTS, 0) == 0


def test_edges_written_counts_both_validated_and_module_imports(make_repo) -> None:
    repo = make_repo(CROSS_FILE_REPO)
    _, edges, stats = _symbols_and_edges(repo)
    assert stats.edges_written() == len(edges)


# --- src-layout import roots (SPEC §6.1) -----------------------------------

# The package lives under src/, so nothing on Jedi's default path contains it.
# `tests/` reaches the implementation only through `import widget.*`, which is
# exactly the edge that vanished before _import_roots existed.
SRC_LAYOUT_REPO: dict[str, str | bytes] = {
    "src/widget/__init__.py": "",
    "src/widget/core.py": (
        "class Widget:\n"
        "    def render(self):\n"
        "        return 'ok'\n"
    ),
    "tests/test_widget.py": (
        "from widget.core import Widget\n"
        "\n"
        "\n"
        "def test_render():\n"
        "    return Widget().render()\n"
    ),
}


def test_import_roots_finds_src_layout(make_repo) -> None:
    repo = make_repo(SRC_LAYOUT_REPO)
    assert [Path(r).name for r in _import_roots(repo)] == ["src"]


def test_import_roots_empty_for_flat_layout(make_repo) -> None:
    """A package at the root needs no extra path entry — and must not get one."""
    repo = make_repo(CROSS_FILE_REPO)
    assert _import_roots(repo) == []


def test_import_roots_skips_ignored_directories(make_repo) -> None:
    repo = make_repo(
        {
            "src/widget/__init__.py": "",
            "node_modules/vendored/__init__.py": "",
        }
    )
    assert [Path(r).name for r in _import_roots(repo)] == ["src"]


def test_test_to_src_edge_resolves_under_src_layout(make_repo) -> None:
    """The regression this exists for: tests -> src resolved to nothing.

    Measured on flask-sqlalchemy before the fix: zero ``tests/`` -> ``src/``
    edges out of 136 test symbols. `GET /coverage` was empty for every file in
    the package as a direct result.
    """
    repo = make_repo(SRC_LAYOUT_REPO)
    symbols, edges, _ = _symbols_and_edges(repo)
    by_key = {(s.file_path, s.start_line): s for s in symbols}
    crossings = {
        (by_key[e.from_key].file_path, by_key[e.to_key].file_path)
        for e in edges
        if e.from_key in by_key and e.to_key in by_key
    }
    assert any(
        frm.startswith("tests/") and to.startswith("src/") for frm, to in crossings
    ), f"no tests/ -> src/ edge among {sorted(crossings)}"
