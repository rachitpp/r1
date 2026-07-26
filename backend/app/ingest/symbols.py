"""Symbol graph extraction: definitions + import/call/extends edges (SPEC §6).

Definitions come free from the Phase 1 tree-sitter pass — every ``RawChunk``
already carries a full dotted qualname and a 1-based line span, so
:func:`extract_symbols` is a projection, not a re-parse.

Edges are the work. Call sites, imports, and class bases are *located* with
tree-sitter (cheap, exact) and *resolved* with Jedi (expensive, fuzzy). Jedi
needs the real files on disk, so this pass runs inside the clone context
before the workdir is deleted.

Resolution is best-effort by design. SPEC §6.1 budgets a per-file wall-clock
timeout (``JEDI_FILE_TIMEOUT_S``) checked between resolutions — no hard
interrupt, no signal handlers, just "stop starting new work on this file".
~20% unresolved overall is expected and acceptable; the rate is logged, not
chased.
"""

from __future__ import annotations

import logging
import time
from bisect import bisect_right
from dataclasses import dataclass, field
from pathlib import Path

import jedi
from tree_sitter import Node

from app.config import JEDI_FILE_TIMEOUT_S
from app.ingest.filters import SourceFile, is_test_path
from app.ingest.parser import ParsedFile, parse_tree

logger = logging.getLogger(__name__)

KIND_IMPORTS = "imports"
KIND_CALLS = "calls"
KIND_EXTENDS = "extends"


@dataclass(frozen=True)
class SymbolRow:
    """One definition, ready for the ``symbols`` table."""

    name: str  # short name
    qualname: str  # pkg.module.Class.method
    kind: str  # function | method | class | module
    file_path: str
    start_line: int
    end_line: int
    is_test: bool


@dataclass(frozen=True)
class EdgeRow:
    """One resolved edge, keyed by (file, start_line) on both ends.

    Symbol ids are assigned by the database, so extraction speaks in the
    natural key and the writer maps it to ids after the symbol insert.
    """

    from_key: tuple[str, int]
    to_key: tuple[str, int]
    kind: str
    line: int | None


@dataclass
class EdgeStats:
    """Per-run resolution accounting (SPEC §6.1 — log the rate, move on).

    A site that yields no edge is **not** automatically a failure. Outcomes:

    ``resolved``       in-repo target found; an edge was written.
    ``out_of_repo``    Jedi resolved it, but to stdlib/site-packages. Dropped
                       by design (§6.1) — a *correct* outcome, not a miss.
    ``no_target``      Jedi returned nothing, or raised. A genuine failure.
    ``unmapped``       resolved in-repo, but the target line falls inside no
                       symbol span (module-level constant, bare assignment).

    Reporting these separately matters: SPEC's "~20% unresolved is expected"
    budgets *failures*, and a codebase that calls `len()` a thousand times
    would blow a combined metric while resolving perfectly.
    """

    sites: dict[str, int] = field(default_factory=dict)
    resolved: dict[str, int] = field(default_factory=dict)
    out_of_repo: dict[str, int] = field(default_factory=dict)
    no_target: dict[str, int] = field(default_factory=dict)
    unmapped: dict[str, int] = field(default_factory=dict)
    timed_out_files: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0

    def site(self, kind: str) -> None:
        self.sites[kind] = self.sites.get(kind, 0) + 1

    def hit(self, kind: str) -> None:
        self.resolved[kind] = self.resolved.get(kind, 0) + 1

    def _bump(self, bucket: dict[str, int], kind: str) -> None:
        bucket[kind] = bucket.get(kind, 0) + 1

    def _rate(self, bucket: dict[str, int], kind: str | None) -> float:
        total = sum(self.sites.values()) if kind is None else self.sites.get(kind, 0)
        got = sum(bucket.values()) if kind is None else bucket.get(kind, 0)
        return 0.0 if total == 0 else got / total

    def failure_rate(self, kind: str | None = None) -> float:
        """Share of sites Jedi could not resolve at all — the SPEC §6.1 metric."""
        return self._rate(self.no_target, kind)

    def out_of_repo_rate(self, kind: str | None = None) -> float:
        """Share correctly dropped as external. Not a failure."""
        return self._rate(self.out_of_repo, kind)

    def unresolved_rate(self, kind: str | None = None) -> float:
        """Share of sites that produced no edge, for any reason (superset)."""
        return 1.0 - self._rate(self.resolved, kind)


# ---------------------------------------------------------------------------
# Definitions
# ---------------------------------------------------------------------------


def extract_symbols(parsed: list[ParsedFile]) -> list[SymbolRow]:
    """Project parsed chunks onto symbol rows, deduped by (file, start_line).

    A chunk and its symbol are 1:1 pre-oversize-split, so this is a rename of
    fields rather than new analysis. `is_test` comes from the file's §2.6
    classification — the same rule Phase 2 applied to chunks.
    """
    seen: set[tuple[str, int]] = set()
    out: list[SymbolRow] = []
    for pf in parsed:
        test = is_test_path(pf.file_path)
        for c in pf.chunks:
            key = (c.file_path, c.start_line)
            if key in seen:
                continue
            seen.add(key)
            out.append(
                SymbolRow(
                    name=c.symbol.rsplit(".", 1)[-1] if c.symbol else "",
                    qualname=c.symbol,
                    kind=c.kind,
                    file_path=c.file_path,
                    start_line=c.start_line,
                    end_line=c.end_line,
                    is_test=test,
                )
            )
    return out


# ---------------------------------------------------------------------------
# Span index — map a resolved (file, line) back to the symbol that owns it
# ---------------------------------------------------------------------------


class _SpanIndex:
    """Innermost-span lookup: which symbol encloses ``file_path:line``?

    Jedi reports a definition's *name* line while our spans start at the
    decorator, so exact-line matching misses decorated defs. Containment with
    an innermost preference handles both, and also gives us the enclosing
    symbol for a call site for free.
    """

    def __init__(self, symbols: list[SymbolRow]) -> None:
        self._by_file: dict[str, list[SymbolRow]] = {}
        for s in symbols:
            self._by_file.setdefault(s.file_path, []).append(s)
        for rows in self._by_file.values():
            rows.sort(key=lambda r: (r.start_line, -r.end_line))
        self._starts: dict[str, list[int]] = {
            f: [r.start_line for r in rows] for f, rows in self._by_file.items()
        }

    def at(self, file_path: str, line: int) -> tuple[str, int] | None:
        """Key of the narrowest symbol whose span contains ``line``."""
        rows = self._by_file.get(file_path)
        if not rows:
            return None
        # Candidates start at or before `line`; scan those for containment.
        cut = bisect_right(self._starts[file_path], line)
        best: SymbolRow | None = None
        for r in rows[:cut]:
            if r.start_line <= line <= r.end_line:
                if best is None or (r.end_line - r.start_line) < (
                    best.end_line - best.start_line
                ):
                    best = r
        return None if best is None else (best.file_path, best.start_line)


# ---------------------------------------------------------------------------
# Site location (tree-sitter)
# ---------------------------------------------------------------------------


def _rightmost_identifier(node: Node) -> Node | None:
    """Deepest trailing identifier of a dotted expression or import clause.

    ``httpx._models.Response`` -> the ``Response`` node. Jedi resolves an
    attribute access from the attribute's own position, not the root's.

    Import statements need three extra shapes: ``dotted_name`` (``pkg.base``
    in an import clause — a flat list of identifiers, *not* an ``attribute``
    chain), and ``aliased_import`` (``X as Y``), where the resolvable name is
    the original, not the alias.
    """
    cur = node
    while True:
        if cur.type == "identifier":
            return cur
        if cur.type == "dotted_name":
            idents = [c for c in cur.named_children if c.type == "identifier"]
            return idents[-1] if idents else None
        if cur.type == "aliased_import":
            name = cur.child_by_field_name("name")
            if name is None:
                return None
            cur = name
            continue
        if cur.type == "attribute":
            attr = cur.child_by_field_name("attribute")
            if attr is None:
                return None
            cur = attr
            continue
        if cur.type == "call":
            fn = cur.child_by_field_name("function")
            if fn is None:
                return None
            cur = fn
            continue
        return None


@dataclass(frozen=True)
class _Site:
    """A location to hand Jedi: 1-based line, 0-based column, edge kind."""

    line: int
    column: int
    kind: str


def _collect_sites(root: Node) -> list[_Site]:
    """Walk the tree once, collecting import / call / base-class positions."""
    sites: list[_Site] = []
    stack: list[Node] = [root]
    while stack:
        node = stack.pop()
        if node.type in ("import_statement", "import_from_statement"):
            for child in node.named_children:
                ident = _rightmost_identifier(child)
                if ident is not None:
                    r, c = ident.start_point
                    sites.append(_Site(r + 1, c, KIND_IMPORTS))
        elif node.type == "call":
            fn = node.child_by_field_name("function")
            ident = _rightmost_identifier(fn) if fn is not None else None
            if ident is not None:
                r, c = ident.start_point
                sites.append(_Site(r + 1, c, KIND_CALLS))
        elif node.type == "class_definition":
            bases = node.child_by_field_name("superclasses")
            if bases is not None:
                for b in bases.named_children:
                    ident = _rightmost_identifier(b)
                    if ident is not None:
                        r, c = ident.start_point
                        sites.append(_Site(r + 1, c, KIND_EXTENDS))
        stack.extend(node.named_children)
    return sites


# ---------------------------------------------------------------------------
# Edge extraction (Jedi)
# ---------------------------------------------------------------------------


def _rel_path(abs_path: Path, repo_dir: Path) -> str | None:
    """Repo-relative posix path, or None for targets outside the repo."""
    try:
        return abs_path.resolve().relative_to(repo_dir.resolve()).as_posix()
    except (ValueError, OSError):
        return None


def extract_edges(
    repo_dir: Path,
    sources: list[SourceFile],
    symbols: list[SymbolRow],
    *,
    timeout_s: int = JEDI_FILE_TIMEOUT_S,
) -> tuple[list[EdgeRow], EdgeStats]:
    """Resolve import/call/extends edges for ``sources`` (SPEC §6.1).

    Targets outside the repo (stdlib, site-packages) are dropped — the graph
    describes this repository, not its dependency tree.
    """
    index = _SpanIndex(symbols)
    stats = EdgeStats()
    seen: set[tuple[tuple[str, int], tuple[str, int], str, int | None]] = set()
    edges: list[EdgeRow] = []
    project = jedi.Project(str(repo_dir))
    run_start = time.perf_counter()

    for source in sources:
        tree = parse_tree(source.text)
        if tree is None or tree.root_node.has_error:
            continue
        sites = _collect_sites(tree.root_node)
        if not sites:
            continue

        abs_path = repo_dir / source.path
        try:
            script = jedi.Script(code=source.text, path=str(abs_path), project=project)
        except Exception as exc:  # jedi can raise on odd encodings/paths
            logger.warning("jedi.Script failed for %s: %s", source.path, exc)
            continue

        file_start = time.perf_counter()
        for site in sites:
            stats.site(site.kind)
            # Budget check *between* resolutions — no hard interrupt (SPEC §6.1).
            if time.perf_counter() - file_start > timeout_s:
                stats.timed_out_files.append(source.path)
                logger.warning(
                    "jedi budget exhausted for %s after %.1fs; skipping remaining edges",
                    source.path,
                    timeout_s,
                )
                break

            from_key = index.at(source.path, site.line)
            if from_key is None:
                continue
            try:
                targets = script.goto(
                    site.line, site.column, follow_imports=True
                )
            except Exception:  # noqa: BLE001 — resolution is best-effort
                stats._bump(stats.no_target, site.kind)
                continue
            if not targets:
                stats._bump(stats.no_target, site.kind)
                continue

            outcome = "out_of_repo"  # until proven otherwise
            for t in targets:
                if t.module_path is None:
                    continue
                rel = _rel_path(Path(t.module_path), repo_dir)
                if rel is None:
                    continue  # outside the repo — dropped by design (§6.1)
                to_key = index.at(rel, t.line or 0)
                if to_key is None:
                    outcome = "unmapped"  # in-repo, but no symbol owns that line
                    continue
                if to_key == from_key:
                    outcome = "resolved"  # self-reference; edge intentionally omitted
                    break
                dedupe = (from_key, to_key, site.kind, site.line)
                if dedupe in seen:
                    outcome = "resolved"
                    break
                seen.add(dedupe)
                edges.append(
                    EdgeRow(
                        from_key=from_key,
                        to_key=to_key,
                        kind=site.kind,
                        line=site.line,
                    )
                )
                stats.hit(site.kind)
                outcome = "resolved"
                break  # first in-repo target wins

            if outcome == "out_of_repo":
                stats._bump(stats.out_of_repo, site.kind)
            elif outcome == "unmapped":
                stats._bump(stats.unmapped, site.kind)

    stats.elapsed_s = time.perf_counter() - run_start
    return edges, stats
