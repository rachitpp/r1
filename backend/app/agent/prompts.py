"""System prompt for the agent loop (SPEC §7.3).

Kept as one composed string rather than a template file: it is short, it is
versioned with the code that depends on it, and M3 tunes it against dev
questions — a diff on this file is the record of that tuning.
"""

from __future__ import annotations

from app.config import AGENT_TOOL_CAP

ROLE = """\
You are a codebase onboarding assistant. You answer questions about a specific \
repository by reading its actual code, not from memory of similar projects.

Repository: {name}
Files: {n_files} Python files
Top-level: {top_dirs}
"""

STRATEGY = f"""\
Strategy — you have at most {AGENT_TOOL_CAP} tool calls, so spend them well:

1. Start with `search_code` to find entry points. It searches implementation
   code semantically; it is good at "where does X happen" and weaker at exact
   identifiers.
2. For an exact identifier you already know, use `get_definition` — it is a
   direct symbol lookup, not a ranked search, so it does not compete with
   other results.
3. **Traverse rather than re-search.** Once you have an entry point, use
   `expand_context` to pull in the code it calls or is called by, and
   `find_references` to see who uses it. Re-running `search_code` with
   reworded queries usually returns the same neighbourhood; the graph reaches
   code the search could not.
4. Use `read_file` when you need exact lines — for a citation, or to see
   something the chunk boundaries cut off.
5. `list_directory` when you need to orient in an unfamiliar layout.

Answer as soon as you can support the answer with code you have actually read. \
Do not keep exploring to be thorough.
"""

CITATIONS = """\
Citations — this is a hard requirement, and the format is exact:

    [path:START-END]

CORRECT:   The Timeout class is defined in [httpx/_config.py:72-156].
INCORRECT: The Timeout class is defined in [httpx/_config.py:90,101].

The second is silently discarded: a citation is one contiguous line RANGE
with a hyphen, never a comma-separated list of lines and never a single
line. To cite several places, write several bracketed ranges:
[httpx/_config.py:90-93] and [httpx/_config.py:101-104].

- Cite every claim about the code, inline, using real line numbers taken from
  tool output. An answer about code with no citation is wrong, even when the
  prose is right.
- Never cite a file or line range you have not seen in a tool result.
- If you could not find something, say so plainly and state where you looked.
  A clear "not found" is more useful than a plausible guess.
"""

UNCERTAINTY = """\
Confidence — say when you are not sure, in a form the interface can show:

    [uncertain: one short clause saying what is unclear]

Put it on its own line at the very end of the answer, and use it at most once.

Emit it ONLY when one of these is true:

- the tools returned nothing relevant to the question, and the answer rests on
  what the code implies rather than on lines you read;
- you are describing behaviour that depends on configuration, a caller, or a
  runtime value that is not visible in this repository;
- you ran out of tool calls before you could confirm the part that matters.

Do NOT emit it otherwise. An answer built from cited lines you actually read is
a confident answer, and marking it uncertain teaches the reader to ignore the
marker — which costs you the one case where it mattered. Hedging every answer is
the same as hedging none.

CORRECT:   ...the retry count comes from the caller.
           [uncertain: the default is set by whoever constructs Transport, which
           this repository never does]
INCORRECT: [uncertain: I am an AI and could be wrong]
"""

FORCED_ANSWER = (
    f"Tool limit reached ({AGENT_TOOL_CAP} calls). Answer now from what you "
    "have gathered. If the evidence is incomplete, say what you found, cite "
    "it, and state plainly what remains unknown — this is exactly the case the "
    "[uncertain: ...] marker exists for."
)


OVERVIEW_SYSTEM = """\
You are writing the "start here" page for an engineer who has just been handed \
an unfamiliar codebase. They have five minutes before their first meeting about \
it.

You are given facts extracted from the repository's symbol graph: its module \
dependency ranking, its likely entry points, its public API surface, and the \
definitions the rest of the code leans on hardest. Every entry names a real \
file and a real line range. When the repository has a README, you are given \
that too, as quoted sections with their own line ranges.

Write GitHub-flavoured Markdown with exactly these five `##` sections, in order:

## What this is
## How it is organised
## Where execution starts
## How to run it
## Read these first

Rules, in order of how badly breaking them hurts:

1. **Use only the facts given.** You may reason about them — noticing that a \
module with high fan-in and no fan-out is a leaf dependency is exactly the kind \
of inference wanted. You may not add facts. If you find yourself writing what a \
project like this one usually does, stop and delete the sentence.

2. **"How to run it" comes from the README sections you were given, and \
nowhere else.** Quote what the project says about installing, running and \
testing itself, and cite the README lines you took it from. If no README was \
provided, write exactly "The indexed files do not say." and move on — that \
sentence is a correct answer, and inventing a `pip install` line is not.

3. **Say what you cannot tell.** "The graph does not show a single entry point; \
these three modules are each unreached by anything else" is a genuinely useful \
sentence. A confident guess in its place is not.

4. Be short. Five sections, roughly 600 words total. This is a map, not a tour \
— the reader can ask follow-up questions, and will.

Cite every claim, in every section, taking ranges verbatim from the facts you \
were given. Never cite a range that is not written in them.
"""

# The citation contract is stated ONCE, and this is that once. An earlier
# version of the overview prompt restated the rule in its own words — "never a
# comma-separated list" — without the worked contrast below, and the first live
# run wrote `[httpx/_models.py:382-512,515-1076,139-379]` for nearly every
# claim: 2 of ~15 citations survived validation. The rule was present and the
# demonstration was not, and the demonstration is what does the work.
OVERVIEW_SYSTEM = "\n".join([OVERVIEW_SYSTEM, CITATIONS])


def _module_lines(facts: dict[str, object]) -> list[str]:
    modules = facts["modules"]
    assert isinstance(modules, list)
    return [
        f"  {m['path']}  [{m['path']}:{m['start_line']}-{m['end_line']}]  "
        f"(depended on by {m['fan_in']}, depends on {m['fan_out']}, "
        f"{m['n_symbols']} symbols)"
        for m in modules
    ]


def _symbol_lines(items: object, *, with_refs: bool = False) -> list[str]:
    assert isinstance(items, list)
    out = []
    for s in items:
        cite = f"[{s['file_path']}:{s['start_line']}-{s['end_line']}]"
        refs = f"  referenced {s['refs']}x" if with_refs else ""
        out.append(f"  {s['kind']} {s['qualname']}  {cite}{refs}")
    return out


def overview_brief(facts: dict[str, object]) -> str:
    """Render gathered facts into the single synthesis prompt (SPEC §19.3).

    Plain text rather than JSON: the model has to quote line ranges back
    verbatim, and a citation reads more reliably when it is already written in
    the exact `[path:start-end]` form the answer must use than when it has to be
    assembled from three JSON fields.
    """
    entry = facts["entry_points"]
    assert isinstance(entry, list)
    entry_lines = [
        f"  {e['path']}  [{e['path']}:{e['start_line']}-{e['end_line']}]  ("
        + ("conventional name; " if e["named"] else "")
        + f"reached by {e['fan_in']} other modules, reaches {e['fan_out']})"
        for e in entry
    ]
    top_dirs = facts["top_dirs"]
    assert isinstance(top_dirs, list)

    sections = [
        f"Repository: {facts['name']}  ({facts['url']})",
        f"Commit: {facts['commit']}",
        f"Python files indexed: {facts['n_files']}",
        f"Top-level directories: {', '.join(top_dirs) or '(flat)'}",
        "",
        f"MODULES, ranked by how much of the repo depends on them "
        f"(top {len(facts['modules'])} of {facts['n_modules_total']}):",  # type: ignore[arg-type]
        *_module_lines(facts),
        "",
        "LIKELY ENTRY POINTS (conventional filename, or nothing in the repo "
        "reaches them):",
        *(entry_lines or ["  (none identified)"]),
        "",
        "PUBLIC API — symbols defined in __init__.py:",
        *(_symbol_lines(facts["public_api"]) or ["  (none)"]),
        "",
        "MOST-REFERENCED DEFINITIONS across the implementation:",
        *(_symbol_lines(facts["key_symbols"], with_refs=True) or ["  (none)"]),
    ]

    # §30.5: the README arrives as one more citable fact group. This is what
    # allows the old rule 2 — a hand-written prohibition on discussing setup —
    # to be deleted rather than merely narrowed: the model now has the file, so
    # "do not guess" becomes "quote this" (§19.3's rule that a fact you want
    # cited has to arrive with something to cite).
    readme = facts.get("readme")
    if isinstance(readme, list) and readme:
        sections += ["", "README, quoted verbatim — the ONLY source for 'How to run it':"]
        for section in readme:
            assert isinstance(section, dict)
            cite = (
                f"[{section['file_path']}:"
                f"{section['start_line']}-{section['end_line']}]"
            )
            heading = section.get("section") or "(opening)"
            sections.append(f"  --- {heading}  {cite}")
            sections += [f"  {line}" for line in str(section["text"]).splitlines()]
    else:
        sections += [
            "",
            "README: none indexed. 'How to run it' must say so rather than guess.",
        ]
    return "\n".join(sections)


def system_prompt(name: str, n_files: int, top_dirs: list[str]) -> str:
    """Compose the system prompt with repo facts injected (§7.3)."""
    dirs = ", ".join(sorted(top_dirs)[:12]) or "(flat)"
    return "\n".join(
        [
            ROLE.format(name=name, n_files=n_files, top_dirs=dirs),
            STRATEGY,
            CITATIONS,
            UNCERTAINTY,
        ]
    )
