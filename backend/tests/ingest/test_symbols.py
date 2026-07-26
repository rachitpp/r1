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
    assert sum(stats.resolved.values()) == len(edges)
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
