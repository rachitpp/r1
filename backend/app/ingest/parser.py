"""tree-sitter extraction of chunk-worthy nodes from a Python file (SPEC §2.3).

Produces, per file: the file-level import statements (for headers) and a list
of :class:`RawChunk` records — one module chunk, one skeleton per class, and
one chunk per top-level function and per method. Nested defs (depth > 1) stay
inside their parent's chunk. Files whose parse tree ``has_error`` are skipped
with a warning; syntax errors never crash the pipeline.

All line numbers are 1-based (tree-sitter rows are 0-based). A decorated
definition's ``start_line`` is its first decorator's line.
"""

from __future__ import annotations

import logging
import textwrap
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import PurePosixPath

import tree_sitter_python as tsp
from tree_sitter import Language, Node, Parser, Tree

from app.ingest.filters import SourceFile

logger = logging.getLogger(__name__)

# Chunk kinds (SPEC §3).
KIND_MODULE = "module"
KIND_CLASS = "class"
KIND_FUNCTION = "function"
KIND_METHOD = "method"


@dataclass(frozen=True)
class RawChunk:
    """A pre-header chunk: metadata + code, before enrichment (SPEC §2.3)."""

    file_path: str
    symbol: str  # full dotted qualname; module path for module chunks
    kind: str
    signature: str | None  # None for module chunks
    start_line: int  # 1-based
    end_line: int  # 1-based
    code: str


@dataclass(frozen=True)
class ParsedFile:
    """Parser output for one file."""

    file_path: str
    imports: list[str] = field(default_factory=list)  # file-level, normalized
    chunks: list[RawChunk] = field(default_factory=list)


@dataclass(frozen=True)
class BodyBlock:
    """A top-level statement of a chunk body, for oversize splitting.

    ``start``/``end`` are 0-based line indices into the chunk's own code,
    inclusive; they let the chunker map each part back to file line numbers.
    """

    text: str
    start: int
    end: int


@lru_cache(maxsize=1)
def _parser() -> Parser:
    """Return a process-wide tree-sitter Python parser."""
    return Parser(Language(tsp.language()))


def parse_tree(text: str) -> Tree | None:
    """Parse ``text`` with the shared Python parser; ``None`` on failure.

    Exposed so the Phase 3 symbol pass (SPEC §6.1) can locate call sites and
    class bases on the same tree-sitter instance the chunker uses, rather
    than standing up a second parser. Callers check ``root_node.has_error``.
    """
    try:
        return _parser().parse(text.encode("utf-8"))
    except Exception as exc:  # noqa: BLE001 — a bad file must not kill ingest
        logger.warning("tree-sitter parse failed: %s", exc)
        return None


def module_path_from_rel(rel_path: str) -> str:
    """Map a repo-relative posix path to a dotted module path.

    ``a/b/c.py`` -> ``a.b.c``; ``a/b/__init__.py`` -> ``a.b``. A root-level
    ``__init__.py`` maps to the empty string.
    """
    p = PurePosixPath(rel_path)
    parts = list(p.parts)
    if p.name == "__init__.py":
        parts = parts[:-1]
    else:
        parts = parts[:-1] + [p.stem]
    return ".".join(parts)


def _qual(*parts: str) -> str:
    """Join non-empty dotted-name components."""
    return ".".join(p for p in parts if p)


def _text(src: bytes, node: Node) -> str:
    return src[node.start_byte : node.end_byte].decode("utf-8")


def _code_from_lines(lines: list[str], start_line: int, end_line: int) -> str:
    """Extract a 1-based inclusive line range and dedent it to column 0.

    Taking whole lines (not byte offsets) keeps every line's original
    indentation, so ``textwrap.dedent`` removes a clean common prefix and the
    result is self-contained, re-parseable code — important for nested
    (indented) methods and the oversize splitter.
    """
    block = "\n".join(lines[start_line - 1 : end_line])
    return textwrap.dedent(block)


def _oneline(text: str) -> str:
    """Collapse internal whitespace to single spaces."""
    return " ".join(text.split())


def _line(node: Node, which: str) -> int:
    """1-based start or end line of a node."""
    point = node.start_point if which == "start" else node.end_point
    return point[0] + 1


def _signature(src: bytes, def_node: Node) -> str:
    """The ``def``/``class`` header line, normalized and without trailing colon.

    Spans from the definition node start (``def``/``async``/``class``) up to the
    body block, so multi-line parameter lists collapse to one line.
    """
    body = def_node.child_by_field_name("body")
    end = body.start_byte if body is not None else def_node.end_byte
    sig = _oneline(src[def_node.start_byte : end].decode("utf-8"))
    if sig.endswith(":"):
        sig = sig[:-1].rstrip()
    return sig


def _unwrap(node: Node) -> tuple[Node, Node]:
    """Return (inner_definition, outer_node).

    For a ``decorated_definition`` the inner is the wrapped function/class and
    the outer (used for ``start_line``) is the decorator-carrying wrapper.
    """
    if node.type == "decorated_definition":
        inner = node.child_by_field_name("definition")
        if inner is not None:
            return inner, node
    return node, node


def _decorator_lines(src: bytes, outer: Node) -> list[str]:
    """Normalized decorator lines (``@deco``) of a decorated definition."""
    if outer.type != "decorated_definition":
        return []
    return [
        _oneline(_text(src, c)) for c in outer.children if c.type == "decorator"
    ]


def _is_docstring(node: Node) -> bool:
    return (
        node.type == "expression_statement"
        and node.child_count == 1
        and node.children[0].type == "string"
    )


def _is_assignment(node: Node) -> bool:
    return (
        node.type == "expression_statement"
        and node.child_count == 1
        and node.children[0].type == "assignment"
    )


def _class_skeleton(src: bytes, inner: Node, outer: Node, signature: str) -> str:
    """Reconstruct a class skeleton: header, docstring, attrs, method sigs."""
    lines: list[str] = []
    lines.extend(_decorator_lines(src, outer))
    lines.append(f"{signature}:")

    body = inner.child_by_field_name("body")
    body_items: list[str] = []
    if body is not None:
        for child in body.named_children:
            if _is_docstring(child) or _is_assignment(child):
                dedented = textwrap.dedent(_text(src, child)).rstrip("\n")
                body_items.append(textwrap.indent(dedented, "    "))
                continue
            m_inner, m_outer = _unwrap(child)
            if m_inner.type == "function_definition":
                for deco in _decorator_lines(src, m_outer):
                    body_items.append(f"    {deco}")
                body_items.append(f"    {_signature(src, m_inner)}: ...")
            elif m_inner.type == "class_definition":
                # Nested class: represent as an elided signature (depth-2, no
                # separate chunk per the depth-1 rule).
                body_items.append(f"    {_signature(src, m_inner)}: ...")

    if not body_items:
        body_items.append("    ...")
    lines.extend(body_items)
    return "\n".join(lines)


def _module_chunk(
    src: bytes, root: Node, rel_path: str, module_path: str
) -> RawChunk | None:
    """Build the module chunk (docstring + imports + top-level assignments)."""
    included: list[Node] = []
    for i, child in enumerate(root.named_children):
        if i == 0 and _is_docstring(child):
            included.append(child)
        elif child.type in ("import_statement", "import_from_statement"):
            included.append(child)
        elif _is_assignment(child):
            included.append(child)

    if not included:
        return None  # trivially empty

    code = "\n".join(_text(src, n) for n in included)
    end_line = max(_line(n, "end") for n in included)
    return RawChunk(
        file_path=rel_path,
        symbol=module_path,
        kind=KIND_MODULE,
        signature=None,
        start_line=1,
        end_line=end_line,
        code=code,
    )


def parse_file(source: SourceFile) -> ParsedFile | None:
    """Parse one file into chunks; return ``None`` if the tree has errors."""
    src = source.text.encode("utf-8")
    tree = _parser().parse(src)
    root = tree.root_node

    if root.has_error:
        logger.warning("skipping %s: tree-sitter parse error", source.path)
        return None

    module_path = module_path_from_rel(source.path)
    lines = source.text.splitlines()

    imports = [
        _oneline(_text(src, c))
        for c in root.named_children
        if c.type in ("import_statement", "import_from_statement")
    ]

    chunks: list[RawChunk] = []

    module_chunk = _module_chunk(src, root, source.path, module_path)
    if module_chunk is not None:
        chunks.append(module_chunk)

    for child in root.named_children:
        inner, outer = _unwrap(child)
        if inner.type == "function_definition":
            name_node = inner.child_by_field_name("name")
            name = _text(src, name_node) if name_node is not None else "<anon>"
            start_line = _line(outer, "start")
            end_line = _line(inner, "end")
            chunks.append(
                RawChunk(
                    file_path=source.path,
                    symbol=_qual(module_path, name),
                    kind=KIND_FUNCTION,
                    signature=_signature(src, inner),
                    start_line=start_line,
                    end_line=end_line,
                    code=_code_from_lines(lines, start_line, end_line),
                )
            )
        elif inner.type == "class_definition":
            chunks.extend(
                _class_chunks(src, lines, inner, outer, source.path, module_path)
            )

    return ParsedFile(file_path=source.path, imports=imports, chunks=chunks)


def body_statement_spans(code: str, kind: str) -> tuple[str, list[BodyBlock]]:
    """Split a chunk's code into a prefix and top-level body statements.

    Used only for oversize handling (SPEC §2.5). For a function/method the
    prefix is the decorators + ``def`` header and the blocks are the body's
    top-level statements; for a module chunk there is no prefix and the blocks
    are the collected statements. Boundaries are always whole statements —
    never mid-statement. Line-based slicing preserves indentation.
    """
    code_lines = code.splitlines()
    root = _parser().parse(code.encode("utf-8")).root_node

    if kind == KIND_MODULE:
        stmts = list(root.named_children)
        prefix_lines = 0
    else:
        if not root.named_children:
            return "", []
        inner, _ = _unwrap(root.named_children[0])
        body = inner.child_by_field_name("body")
        stmts = list(body.named_children) if body is not None else []
        if not stmts:
            return "", []
        prefix_lines = stmts[0].start_point[0]

    prefix = "\n".join(code_lines[:prefix_lines])
    blocks = [
        BodyBlock(
            text="\n".join(code_lines[s.start_point[0] : s.end_point[0] + 1]),
            start=s.start_point[0],
            end=s.end_point[0],
        )
        for s in stmts
    ]
    return prefix, blocks


def _class_chunks(
    src: bytes,
    lines: list[str],
    inner: Node,
    outer: Node,
    rel_path: str,
    module_path: str,
) -> list[RawChunk]:
    """Skeleton chunk for a class plus one chunk per direct method."""
    name_node = inner.child_by_field_name("name")
    class_name = _text(src, name_node) if name_node is not None else "<anon>"
    class_qual = _qual(module_path, class_name)
    signature = _signature(src, inner)

    out: list[RawChunk] = [
        RawChunk(
            file_path=rel_path,
            symbol=class_qual,
            kind=KIND_CLASS,
            signature=signature,
            start_line=_line(outer, "start"),
            end_line=_line(inner, "end"),
            code=_class_skeleton(src, inner, outer, signature),
        )
    ]

    body = inner.child_by_field_name("body")
    if body is None:
        return out
    for child in body.named_children:
        m_inner, m_outer = _unwrap(child)
        if m_inner.type != "function_definition":
            continue
        mname_node = m_inner.child_by_field_name("name")
        mname = _text(src, mname_node) if mname_node is not None else "<anon>"
        start_line = _line(m_outer, "start")
        end_line = _line(m_inner, "end")
        out.append(
            RawChunk(
                file_path=rel_path,
                symbol=_qual(class_qual, mname),
                kind=KIND_METHOD,
                signature=_signature(src, m_inner),
                start_line=start_line,
                end_line=end_line,
                code=_code_from_lines(lines, start_line, end_line),
            )
        )
    return out
