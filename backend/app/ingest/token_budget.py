"""Token counting for oversize-split decisions (SPEC §2.5).

Phase 1 uses a character-heuristic stand-in; the real embedding tokenizer
arrives in Phase 2 with sentence-transformers (native deps not installed in
Phase 1) and is swapped in behind the same :class:`TokenCounter` protocol.
See DECISIONS.md ("Heuristic token counter in Phase 1").
"""

from __future__ import annotations

from typing import Protocol


class TokenCounter(Protocol):
    """Anything that can estimate the token length of a string."""

    def token_len(self, text: str) -> int: ...


class HeuristicTokenCounter:
    """Approximate token count as ``len(text) // 4``.

    A crude but stable proxy for BPE tokenizers on English/code text. Only the
    oversize threshold (``CHUNK_TOKEN_MAX``) depends on it; AST chunk
    boundaries do not. Phase 2 replaces this with the model tokenizer and
    re-checks splits.
    """

    def token_len(self, text: str) -> int:
        return len(text) // 4
