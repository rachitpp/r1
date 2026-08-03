"""Citation grounding: does the cited range actually support the claim (§27).

FEATURE-IDEAS 5.1. `citations.validate_citations` already answers *"is this a
real place in this repo"* — it drops fabricated paths and clamps overshot
ranges. It cannot answer *"does the code there say what the sentence says"*,
and that is the subtler failure: a citation that is **present but wrong** looks
exactly like a good one to a reader, which is worse than no citation at all,
because a missing citation is visibly missing.

**This is a heuristic, not a model call, and that is a deliberate choice.**
FEATURE-IDEAS 5.1 allows either ("cheap model call or heuristic"). A model pass
costs one extra request per answer on a tier already measured at 20
requests/day for one provider, adds latency to the critical path, and — worst —
makes the grounding signal itself non-deterministic, so the same answer could be
flagged differently on two runs. A lexical check is free, instant, reproducible,
and can be *wrong in a way a reader can see and overrule*, which is the right
shape for an advisory signal.

**The check.** For each citation, take the claim it is attached to (the sentence
or bullet it terminates), pull the code identifiers out of that claim, and ask
whether any of them appear in the lines actually cited.

**Three outcomes, and the third is the honest one.** `supported` and
`unsupported` are the obvious two. `unchecked` exists because a claim naming no
identifiers at all — "this is where the request begins" — offers nothing to
match, and reporting that as *unsupported* would manufacture a warning out of
the method's own blind spot. Silence is the correct output for a question this
technique cannot ask.
"""

from __future__ import annotations

import re
from typing import Literal, TypedDict

from app.agent.citations import CITATION_RE, Citation

# Backticked spans are the high-precision signal: the answer format uses them
# for code (`Signal.connect`, `self._by_sender`), so a backtick is the model
# telling us "this is an identifier" rather than us guessing from shape.
_BACKTICKED = re.compile(r"`([^`\n]{1,120})`")

# Identifier-shaped tokens outside backticks. Deliberately broader than
# `retrieval.hybrid.extract_identifiers`, which drops plain lowercase words:
# that is right when injecting symbols into a search (`get` would match half the
# repo) and wrong here, because a claim like "connect registers the receiver"
# names `connect` and the cited lines either contain it or do not.
_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}(?:\.[A-Za-z_][A-Za-z0-9_]*)*")

# Words that look like identifiers and carry no evidence. Matching on these
# would mark almost everything `supported`, which is the failure mode that makes
# a grounding signal worthless — a check that always passes is not a check.
_STOPWORDS = frozenset(
    {
        "the", "and", "for", "with", "that", "this", "from", "then", "when",
        "where", "which", "into", "returns", "return", "value", "values",
        "call", "calls", "called", "method", "methods", "function", "functions",
        "class", "classes", "module", "modules", "object", "objects", "code",
        "line", "lines", "file", "files", "here", "there", "each", "also",
        "same", "only", "both", "keeps", "keep", "uses", "used", "using",
        "does", "doing", "done", "make", "makes", "made", "set", "sets",
        "get", "gets", "not", "but", "are", "was", "were", "has", "have",
        "its", "their", "them", "they", "one", "two", "any", "all", "can",
        "will", "would", "should", "must", "may", "might", "because", "since",
        "rather", "than", "over", "under", "after", "before", "while",
    }
)

Verdict = Literal["supported", "unsupported", "unchecked"]


class Grounding(TypedDict):
    """One citation's grounding verdict."""

    file_path: str
    start_line: int
    end_line: int
    verdict: Verdict
    #: Identifiers from the claim that were found in the cited lines.
    matched: list[str]
    #: Identifiers from the claim that were not.
    missing: list[str]


def claim_for(answer: str, citation_end: int) -> str:
    """The text a citation is attached to: back to the previous boundary.

    A citation terminates a claim, so the evidence for it is what precedes it —
    to the start of the sentence, the bullet, or the line, whichever is nearest.
    Taking the whole paragraph would drag in neighbouring claims and their
    identifiers, which is how a lexical check quietly becomes a check that
    everything passes.

    **Boundaries are skipped while they yield nothing.** A model that
    hard-wraps its output puts the citation on its own continuation line::

        - `build_chunks` reads every PDF and splits prose into chunks
          [app.py:404-449]

    and stopping at the nearest newline then hands back two spaces. Every
    wrapped citation would score `unchecked` — silently, since that is also
    what an uncheckable claim looks like. SPEC §25.3 already collapses
    hard-wrapped uncertainty markers for the same reason; this is the same
    shape one feature over.
    """
    # Strip citation markers first: another citation's path is not evidence
    # about this one, and leaving them in makes an otherwise-blank window look
    # non-empty.
    head = CITATION_RE.sub(" ", answer[:citation_end])

    # Three hops is enough for a wrapped line and a stray blank line; more
    # would start pulling in the previous claim, which is the failure the
    # boundary exists to prevent.
    for _ in range(3):
        boundary = max(
            head.rfind("\n"),
            head.rfind(". "),
            head.rfind("? "),
            head.rfind(": "),
        )
        candidate = head[boundary + 1 :] if boundary >= 0 else head
        if candidate.strip():
            return candidate.strip()
        if boundary < 0:
            break
        head = head[:boundary]
    return ""


def claim_identifiers(claim: str) -> list[str]:
    """Code identifiers a claim names, backticked spans first.

    Dotted names contribute their parts too: a claim citing `Signal.connect`
    is supported by lines defining `connect` inside a `Signal` class, and
    requiring the literal dotted string would call that unsupported.
    """
    found: list[str] = []
    seen: set[str] = set()

    def add(token: str, *, backticked: bool = False) -> None:
        """Record a candidate identifier.

        Stopwords are **not** applied to backticked spans. The backtick is the
        model explicitly marking a token as code, and overriding that with an
        English word list throws away real identifiers that happen to spell a
        common word: blinker's sentinel is literally `ANY`, and a claim naming
        it was scored `unchecked` until this distinction existed. Outside
        backticks the list still earns its place — matching on "the" or
        "method" would make almost everything `supported`.
        """
        token = token.strip().strip("()[]{}.,:;\"'")
        if not token or len(token) < 3:
            return
        if not backticked and token.lower() in _STOPWORDS:
            return
        if token not in seen:
            seen.add(token)
            found.append(token)

    for span in _BACKTICKED.findall(claim):
        add(span, backticked=True)
        for part in re.split(r"[.\s(]+", span):
            add(part, backticked=True)

    without_ticks = _BACKTICKED.sub(" ", claim)
    for token in _TOKEN.findall(without_ticks):
        # Outside backticks, require identifier *shape* — an underscore, a dot,
        # or CamelCase. Bare prose words are not evidence, and a claim's ordinary
        # English would otherwise match any code containing the same word.
        if "_" in token or "." in token or _is_camel(token):
            add(token)
            for part in token.split("."):
                add(part)
    return found


def _is_camel(token: str) -> bool:
    return any(c.isupper() for c in token[1:]) and any(c.islower() for c in token)


def ground_citation(
    answer: str, citation: Citation, cited_lines: str, citation_end: int
) -> Grounding:
    """Verdict for one citation against the lines it points at."""
    claim = claim_for(answer, citation_end)
    identifiers = claim_identifiers(claim)
    base: Grounding = {
        "file_path": citation["file_path"],
        "start_line": citation["start_line"],
        "end_line": citation["end_line"],
        "verdict": "unchecked",
        "matched": [],
        "missing": [],
    }
    if not identifiers:
        return base

    haystack = cited_lines
    matched = [i for i in identifiers if i in haystack]
    missing = [i for i in identifiers if i not in haystack]
    base["matched"] = matched
    base["missing"] = missing
    base["verdict"] = "supported" if matched else "unsupported"
    return base


def ground_answer(
    answer: str, citations: list[Citation], text_of: dict[str, str]
) -> list[Grounding]:
    """Ground every citation in ``answer``.

    ``text_of`` maps file path to full file content; the cited slice is taken
    here rather than by the caller so the line arithmetic (1-based, inclusive)
    lives in one place.

    Citations whose file is missing from ``text_of`` come back ``unchecked``
    rather than ``unsupported`` — a file we could not read is a gap in *this*
    check, not evidence against the answer.
    """
    ends: dict[tuple[str, int, int], int] = {}
    for m in CITATION_RE.finditer(answer):
        key = (m.group(1), int(m.group(2)), int(m.group(3)))
        ends.setdefault(key, m.end())

    out: list[Grounding] = []
    for c in citations:
        key = (c["file_path"], c["start_line"], c["end_line"])
        end = ends.get(key, len(answer))
        text = text_of.get(c["file_path"])
        if text is None:
            out.append(
                {
                    "file_path": c["file_path"],
                    "start_line": c["start_line"],
                    "end_line": c["end_line"],
                    "verdict": "unchecked",
                    "matched": [],
                    "missing": [],
                }
            )
            continue
        lines = text.splitlines()
        slice_ = "\n".join(lines[c["start_line"] - 1 : c["end_line"]])
        out.append(ground_citation(answer, c, slice_, end))
    return out
