"""Work around a langchain-core bug that crashes answers containing "index".

**The bug.** `langchain_core.utils._merge.merge_lists` assembles streamed
message chunks. When the accumulated content is a *list* and a later chunk is a
content block, it looks for a block with a matching index::

    for i, e_left in enumerate(merged)
    if ("index" in e_left and e_left["index"] == e["index"] and ...)

Nothing checks that ``e_left`` is a dict. When a provider emits a plain string
first and structured blocks after — which Mistral does — ``merged`` holds a
``str``, and ``"index" in e_left`` silently becomes a **substring test**. It
passes whenever the answer happens to contain the word "index", and the next
line raises ``TypeError: string indices must be integers``.

**Why this project hits it constantly.** It is a codebase *indexing* tool. "the
symbol index", "indexed at ingest", "index the repo" — the crashing substring is
in the domain vocabulary, so the failure is not the rare edge case it looks
like. Observed live: an answer streamed to the user in full and was then
followed by "Something went wrong", losing its citations.

Present in 1.5.1 and still present in 1.5.3 (checked against the published
sdist), so upgrading is not the fix.

**The patch is inert unless the original raises.** It calls upstream first and
only retries — with strings in the accumulator promoted to text blocks, so the
index comparison sees dicts — when upstream raises ``TypeError``. If a future
release fixes the guard, this wrapper stops doing anything at all without
needing to be found and removed. That is the property worth having in a
monkeypatch: it cannot cause the bug it exists to avoid.
"""

from __future__ import annotations

import functools
import logging
import sys
from typing import Any

logger = logging.getLogger(__name__)

_MARK = "_app_merge_guard"


def _as_blocks(items: Any) -> Any:
    """Promote bare strings in a content list to text blocks.

    Only strings are touched, and only their container is rebuilt — this is the
    shape langchain itself uses for text content, so the retry hands upstream
    something it already knows how to merge.
    """
    if not isinstance(items, list):
        return items
    return [
        {"type": "text", "text": item} if isinstance(item, str) else item
        for item in items
    ]


def _guard(original: Any) -> Any:
    @functools.wraps(original)
    def merge_lists(left: Any, *others: Any) -> Any:
        try:
            return original(left, *others)
        except TypeError:
            # The only known cause is the mixed str/dict accumulator above.
            # Retry once with the strings promoted; if that also fails, the
            # cause is something else and the error belongs to the caller.
            logger.debug("merge_lists: retrying with promoted text blocks")
            return original(_as_blocks(left), *(_as_blocks(o) for o in others))

    setattr(merge_lists, _MARK, True)
    return merge_lists


def apply() -> int:
    """Install the guard everywhere ``merge_lists`` is already bound.

    langchain-core re-exports the function by value (``from ... import
    merge_lists``) in several modules, so patching the defining module alone
    would miss `messages.base`, which is the one that actually crashes.
    Rebinding by identity across `sys.modules` catches every importer without
    naming them, and naming them is exactly the list that would go stale.

    Returns the number of bindings replaced; 0 means it was already applied.
    """
    from langchain_core.utils import _merge

    original = _merge.merge_lists
    if getattr(original, _MARK, False):
        return 0

    patched = _guard(original)
    replaced = 0
    for name, module in list(sys.modules.items()):
        # `vars(module)`, never `getattr(module, ...)`. A lazy-import package
        # implements `__getattr__` to import a submodule on demand, so probing
        # an arbitrary attribute *executes code*: `getattr(transformers,
        # "merge_lists")` sent transformers off to import an image processor
        # and died on a missing torchvision. Reading `__dict__` cannot trigger
        # that. The name filter is belt and braces on top.
        if not name.startswith("langchain"):
            continue
        if vars(module).get("merge_lists") is original:
            module.merge_lists = patched  # type: ignore[attr-defined]
            replaced += 1
    if replaced:
        logger.info("patched langchain merge_lists in %d module(s)", replaced)
    return replaced
