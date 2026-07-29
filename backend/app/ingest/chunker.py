"""Assemble enrichment headers and handle oversize splitting (SPEC §2.4-2.5).

Turns :class:`RawChunk` records from the parser into final :class:`Chunk`
records: ``header + "\\n---\\n" + code``. Chunks whose token length exceeds
``CHUNK_TOKEN_MAX`` are split on top-level statement boundaries into parts,
each repeating the full header with a ``# Part: i/n`` line. Never splits
mid-statement or on character counts.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import CHUNK_TOKEN_MAX
from app.ingest.parser import BodyBlock, ParsedFile, RawChunk, body_statement_spans
from app.ingest.tokens import TokenCounter

SEPARATOR = "\n---\n"

# Kinds whose `code` is *assembled* rather than sliced out of the file. A class
# chunk is a skeleton — method bodies elided (SPEC §2.2) — and a module chunk
# gathers the docstring, the import block, and top-level assignments, stepping
# over every def and class between them. For both, an offset into the chunk's
# own text is not an offset into the file, so the per-part arithmetic below
# would invent line numbers: on `httpx._client.AsyncClient` it numbered the 18
# parts 1307-1351 then 1352, 1353, 1354… one line per elided method, for a class
# that really ends at line 2019.
#
# Every part of such a chunk reports the whole node span instead. Less precise
# than a true sub-range, and never wrong — which is the right trade when the
# number is a citation the viewer highlights (hard rule 5).
RENDERED_KINDS = frozenset({"class", "module"})


@dataclass(frozen=True)
class Chunk:
    """A final, embeddable chunk (SPEC §3, minus DB/embedding fields)."""

    file_path: str
    symbol: str
    kind: str
    part: int
    n_parts: int
    start_line: int
    end_line: int
    header: str
    code: str

    @property
    def text(self) -> str:
        """The embedded text: header + separator + code."""
        return self.header + SEPARATOR + self.code


def _header(rc: RawChunk, imports_line: str, part: int, n_parts: int) -> str:
    lines = [
        f"# File: {rc.file_path}",
        f"# Symbol: {rc.symbol}",
        f"# Kind: {rc.kind}",
    ]
    if rc.signature is not None:
        lines.append(f"# Signature: {rc.signature}")
    lines.append(f"# Imports: {imports_line}")
    if n_parts > 1:
        lines.append(f"# Part: {part}/{n_parts}")
    return "\n".join(lines)


def _pack(
    prefix: str,
    blocks: list[BodyBlock],
    budget: int,
    counter: TokenCounter,
) -> list[tuple[str, int, int]]:
    """Greedily group body statements into parts under ``budget`` tokens.

    Returns ``(code, start_idx, end_idx)`` per part, where the indices are
    0-based line offsets into the chunk's own code. The first part carries the
    ``prefix`` (decorators + ``def`` line); a single statement larger than the
    budget stands alone rather than being split.
    """
    parts: list[tuple[str, int, int]] = []
    cur: list[BodyBlock] = []

    def render(block_list: list[BodyBlock], is_first: bool) -> str:
        segs = [prefix] if (is_first and prefix) else []
        segs.extend(b.text for b in block_list)
        return "\n".join(segs)

    def finalize(block_list: list[BodyBlock], is_first: bool) -> tuple[str, int, int]:
        start_idx = 0 if (is_first and prefix) else block_list[0].start
        return render(block_list, is_first), start_idx, block_list[-1].end

    for blk in blocks:
        is_first = not parts
        tentative = render(cur + [blk], is_first)
        if cur and counter.token_len(tentative) > budget:
            parts.append(finalize(cur, is_first))
            cur = [blk]
        else:
            cur.append(blk)
    if cur:
        parts.append(finalize(cur, not parts))
    return parts


def _emit(rc: RawChunk, imports_line: str, counter: TokenCounter) -> list[Chunk]:
    single_header = _header(rc, imports_line, 1, 1)
    if counter.token_len(single_header + SEPARATOR + rc.code) <= CHUNK_TOKEN_MAX:
        return [
            Chunk(
                file_path=rc.file_path,
                symbol=rc.symbol,
                kind=rc.kind,
                part=1,
                n_parts=1,
                start_line=rc.start_line,
                end_line=rc.end_line,
                header=single_header,
                code=rc.code,
            )
        ]

    prefix, blocks = body_statement_spans(rc.code, rc.kind)
    if len(blocks) <= 1:
        # Nothing to split on (single huge statement / unsplittable body):
        # emit as one over-budget chunk rather than fabricating a split.
        return [
            Chunk(
                file_path=rc.file_path,
                symbol=rc.symbol,
                kind=rc.kind,
                part=1,
                n_parts=1,
                start_line=rc.start_line,
                end_line=rc.end_line,
                header=single_header,
                code=rc.code,
            )
        ]

    # Budget the body against a header that includes a Part line.
    est_header = _header(rc, imports_line, 1, 2)
    budget = max(CHUNK_TOKEN_MAX - counter.token_len(est_header + SEPARATOR), 1)
    packed = _pack(prefix, blocks, budget, counter)
    n = len(packed)

    rendered = rc.kind in RENDERED_KINDS
    chunks: list[Chunk] = []
    for i, (code_i, s_idx, e_idx) in enumerate(packed, start=1):
        chunks.append(
            Chunk(
                file_path=rc.file_path,
                symbol=rc.symbol,
                kind=rc.kind,
                part=i,
                n_parts=n,
                start_line=rc.start_line if rendered else rc.start_line + s_idx,
                end_line=rc.end_line if rendered else rc.start_line + e_idx,
                header=_header(rc, imports_line, i, n),
                code=code_i,
            )
        )
    return chunks


def chunk_file(parsed: ParsedFile, counter: TokenCounter) -> list[Chunk]:
    """Assemble all chunks for one parsed file, splitting oversize ones."""
    imports_line = "; ".join(parsed.imports)
    out: list[Chunk] = []
    for rc in parsed.chunks:
        out.extend(_emit(rc, imports_line, counter))
    return out
