"""The onboarding checklist (SPEC §22, FEATURE-IDEAS 6.5).

*"The first five things to understand about this repo"*, each one a real file
and line range and each one a question you can ask with a click.

**No model call.** FEATURE-IDEAS pairs 6.5 with 3.1, and the obvious reading is
"a second generated document" — a second request per snapshot against a tier
that allows twenty a day. But §18.1 already settled the shape of this decision:
*if the symbol graph can answer it exactly, it is a query.* Which module
everything imports, where execution starts, which definition the code leans on
hardest, what the package exports, what the tests exercise most — every one of
those is a `GROUP BY` that §19 was already running. What a model would add here
is phrasing, and phrasing is what a template is for.

So this module is **pure**: rows in, ranked items out, no I/O and no prompt. The
route does the SQL, this decides what is worth saying, and the split is what
lets the ordering be tested without a database.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

# Five is the promise in the name, and it is also about the limit of what
# survives being called "first". A longer list is a table of contents.
CHECKLIST_MAX_ITEMS = 5


@dataclass(frozen=True)
class ChecklistItem:
    """One step, with somewhere to look and something to ask."""

    kind: str
    title: str
    detail: str
    file_path: str
    start_line: int
    end_line: int
    question: str


# A step points somewhere; it does not quote the whole definition. Flask's
# `Flask` class is 1516 lines, and a citation that size tells a reader nothing
# about where to start.
STEP_MAX_LINES = 40


def _capped(start: int, end: int) -> int:
    return max(start, min(end, start + STEP_MAX_LINES))


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _package_surface(
    api_symbols: Sequence[Mapping[str, Any]], *, hub_path: str | None
) -> list[Mapping[str, Any]]:
    """Narrow a repo-wide `__all__` scan to *this package's* public surface.

    Caught by looking at real output rather than by a test. `public_api_symbols`
    scans every `__all__` in the repo, which on flask means the winner was
    `examples/celery/src/task_app/__init__.py` — a demo app — announcing
    `celery_init_app` and `create_app` as though they were flask's API. A
    checklist whose "what does this package expose" step points at an example
    directory is worse than no step: it is confidently wrong to exactly the
    reader who cannot yet tell.

    The fix uses a fact the checklist already has. The hub module is the thing
    the repo depends on most, so its directory is the package; anything outside
    it is a sibling, an example, or a script. Falls back to the unfiltered list
    when there is no hub or nothing shares its directory — a wrong-looking
    answer beats a missing one here, because the names are still real.

    Within the package, `__init__.py` wins. That is not a heuristic but the
    language's own answer to "what does this package export" — and without it
    httpx reported its public surface as `main`, the console-script entry in
    `_main.py`, while `httpx/__init__.py` sat there declaring the actual API.

    Duplicate names are dropped too: flask's scan yields `create_app` twice from
    two different examples, and a surface that repeats itself reads as a bug.
    """
    scoped = list(api_symbols)
    if hub_path and "/" in hub_path:
        package = hub_path.rsplit("/", 1)[0] + "/"
        inside = [s for s in api_symbols if str(s["file_path"]).startswith(package)]
        if inside:
            scoped = inside
        declared = [
            s for s in scoped if str(s["file_path"]) == f"{package}__init__.py"
        ]
        if declared:
            scoped = declared

    seen: set[str] = set()
    unique: list[Mapping[str, Any]] = []
    for s in scoped:
        name = str(s["name"])
        if name in seen:
            continue
        seen.add(name)
        unique.append(s)
    return unique


def build_checklist(
    *,
    entry_points: Sequence[Mapping[str, Any]],
    modules: Sequence[Mapping[str, Any]],
    key_symbols: Sequence[Mapping[str, Any]],
    api_symbols: Sequence[Mapping[str, Any]],
    tested_files: Sequence[Mapping[str, Any]],
    n_lines_of: dict[str, int],
    max_items: int = CHECKLIST_MAX_ITEMS,
) -> list[ChecklistItem]:
    """Assemble the checklist, in reading order.

    **Order is narrative, not score.** Each input is already ranked by its own
    query, so sorting the five against each other would be comparing fan-in to
    test counts — different units, no meaning. Instead they are laid out as a
    path through the repo: where it starts, what everything leans on, the one
    definition to read first, what it exposes, and how it is exercised. A
    newcomer following it in order learns the repo in the order it was built.

    **Every step is optional.** A library has no entry point, a repo with no
    resolved test edges has no test step, and a package with no `__all__` has no
    declared surface. Missing steps are simply absent — a checklist that pads
    itself to five with a weak item teaches the reader to skim it.
    """
    items: list[ChecklistItem] = []

    def end_of(path: str, start: int) -> int:
        return _capped(start, n_lines_of.get(path, start))

    # 1. Where execution starts. First because it is the question a newcomer
    #    asks before they know enough to ask a better one.
    if entry_points:
        e = entry_points[0]
        path = str(e["path"])
        items.append(
            ChecklistItem(
                kind="entry_point",
                title="Find where execution starts",
                detail=f"`{path}` looks like an entry point — a conventional "
                "name, or nothing in the repo imports it.",
                file_path=path,
                start_line=1,
                end_line=end_of(path, 1),
                question=f"What happens when {path} runs, and what does it call first?",
            )
        )

    # 2. The hub. Ranked by fan-in, the same number the §18.2 panel ranks by, so
    #    the checklist and the architecture view cannot disagree about it.
    hub = next((m for m in modules if int(m["fan_in"]) > 0), None)
    if hub is not None:
        path = str(hub["path"])
        items.append(
            ChecklistItem(
                kind="hub",
                title="Read the module everything depends on",
                detail=f"`{path}` is imported or called from "
                f"{_plural(int(hub['fan_in']), 'other module')}. Whatever it "
                "defines is vocabulary the rest of the codebase assumes.",
                file_path=path,
                start_line=1,
                end_line=end_of(path, 1),
                question=f"What does {path} define, and why does so much of the repo depend on it?",
            )
        )

    # 3. The single definition to read first — fan-in says which file, this says
    #    which thing inside it.
    if key_symbols:
        k = key_symbols[0]
        items.append(
            ChecklistItem(
                kind="key_symbol",
                title=f"Understand `{k['name']}`",
                detail=f"Referenced from {_plural(int(k['refs']), 'other file')}"
                " — the definition the implementation leans on hardest.",
                file_path=str(k["file_path"]),
                start_line=int(k["start_line"]),
                # Capped like every other step. A 1500-line class is a real
                # extent and a useless pointer: "look here" wants a screenful,
                # and the viewer shows the rest once you arrive.
                end_line=_capped(int(k["start_line"]), int(k["end_line"])),
                question=f"What is {k['qualname']} responsible for, and who uses it?",
            )
        )

    # 4. The declared surface. Distinct from the hub: what a *user* of the
    #    package touches, rather than what the package leans on internally.
    surface = _package_surface(api_symbols, hub_path=str(hub["path"]) if hub else None)
    if surface:
        a = surface[0]
        names = ", ".join(f"`{s['name']}`" for s in surface[:4])
        items.append(
            ChecklistItem(
                kind="public_api",
                title="See what the package exposes",
                detail=f"The declared public surface starts with {names}"
                f"{' …' if len(surface) > 4 else ''}.",
                file_path=str(a["file_path"]),
                start_line=int(a["start_line"]),
                end_line=_capped(int(a["start_line"]), int(a["end_line"])),
                question="What is the public API of this package, and what is the "
                "smallest example that uses it?",
            )
        )

    # 5. How it is exercised. Last on purpose: tests are the best documentation
    #    in the repo and the least useful thing to read before you know the
    #    vocabulary the four steps above establish.
    if tested_files:
        t = tested_files[0]
        path = str(t["file_path"])
        items.append(
            ChecklistItem(
                kind="most_tested",
                title="Read the tests for the busiest module",
                detail=f"`{path}` is reached by {_plural(int(t['n_tests']), 'test')}"
                " — executable documentation for the behaviour that matters most.",
                file_path=path,
                # The top of the module, not its first symbol. This step is
                # about the *file* the suite exercises; starting at whichever
                # definition happens to sit highest also made it collide with
                # the public-surface step on flask, where both landed on
                # `app.py:109` and read as one item printed twice.
                start_line=1,
                end_line=end_of(path, 1),
                question=f"Which tests cover {path}, and what behaviour do they pin?",
            )
        )

    return items[:max_items]
