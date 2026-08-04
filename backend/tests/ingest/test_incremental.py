"""Incremental re-indexing: reuse a vector rather than re-derive it (§29).

An embedding is a pure function of the chunk text; the chunk text is a pure
function of the file content and the chunker. When both are known identical,
re-embedding spends a forward pass to reproduce bytes already in the row —
measured on flask as 54.07 s of work to arrive at 1520 chunks that had not
changed.

These test the decision, not the SQL: which files are eligible, and what
happens when anything about the pairing is uncertain.
"""

from __future__ import annotations

import hashlib
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.ingest import pipeline
from app.ingest.filters import SelectionResult, SourceFile

SNAP = uuid4()
SOURCE = uuid4()
PRIOR = uuid4()


def _file(path: str, text: str) -> SourceFile:
    return SourceFile(path=path, text=text, n_lines=text.count("\n"))


def _digest(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()  # noqa: S324


def _selection(files: list[SourceFile]) -> SelectionResult:
    return SelectionResult(
        files=files,
        n_candidates=len(files),
        skipped_unsupported=0,
        skipped_ignored_dir=0,
        skipped_too_large=0,
        skipped_binary=0,
        skipped_decode_error=0,
    )


class FakeQueries:
    """Stands in for the three §29 reads/writes."""

    def __init__(
        self, prior: UUID | None, digests: dict[str, str] | None = None
    ) -> None:
        self.prior = prior
        self.digests = digests or {}
        self.copied: list[str] = []
        self.asked: dict[str, Any] = {}

    async def reusable_snapshot(self, conn, source_id, **kw):  # noqa: ANN001
        self.asked = dict(source_id=source_id, **kw)
        return self.prior

    async def file_digests(self, conn, snapshot_id):  # noqa: ANN001
        return self.digests

    async def copy_chunks(self, conn, frm, to, paths):  # noqa: ANN001
        self.copied = list(paths)
        return len(paths) * 10  # ten chunks per file, arbitrary but countable


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    def _install(q: FakeQueries) -> FakeQueries:
        for name in ("reusable_snapshot", "file_digests", "copy_chunks"):
            monkeypatch.setattr(pipeline.queries, name, getattr(q, name))
        return q

    return _install


async def _run(selection: SelectionResult) -> tuple[set[str], int]:
    return await pipeline._reuse_unchanged_chunks(
        None,
        SNAP,
        source_id=SOURCE,
        strategy="ast",
        selection=selection,
        say=lambda _m: None,
    )


async def test_no_prior_snapshot_reuses_nothing(fake) -> None:  # noqa: ANN001
    """The first ingest of a repo, and it must cost nothing to ask."""
    fake(FakeQueries(prior=None))
    assert await _run(_selection([_file("a.py", "x = 1\n")])) == (set(), 0)


async def test_an_unchanged_file_is_reused(fake) -> None:  # noqa: ANN001
    q = fake(FakeQueries(prior=PRIOR, digests={"a.py": _digest("x = 1\n")}))
    paths, copied = await _run(_selection([_file("a.py", "x = 1\n")]))
    assert paths == {"a.py"}
    assert copied == 10
    assert q.copied == ["a.py"]


async def test_a_changed_file_is_not_reused(fake) -> None:  # noqa: ANN001
    """One byte different is a different chunk and a different vector."""
    fake(FakeQueries(prior=PRIOR, digests={"a.py": _digest("x = 1\n")}))
    assert await _run(_selection([_file("a.py", "x = 2\n")])) == (set(), 0)


async def test_a_new_file_is_not_reused(fake) -> None:  # noqa: ANN001
    fake(FakeQueries(prior=PRIOR, digests={"a.py": _digest("x = 1\n")}))
    paths, _ = await _run(
        _selection([_file("a.py", "x = 1\n"), _file("b.py", "y = 2\n")])
    )
    assert paths == {"a.py"}


async def test_the_lookup_is_scoped_to_strategy_and_model(fake) -> None:  # noqa: ANN001
    """Both are correctness conditions, not filters.

    `naive` and `ast` cut different chunks from identical text, and vectors
    from two embedding models do not share a space — cosine distance across
    them returns plausible numbers and wrong answers.
    """
    q = fake(FakeQueries(prior=None))
    await _run(_selection([_file("a.py", "x = 1\n")]))
    assert q.asked["strategy"] == "ast"
    assert q.asked["embedding_model"]  # whatever config says, it is passed
    assert q.asked["exclude"] == SNAP


async def test_a_failure_degrades_to_a_full_re_embed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reuse is an optimisation; losing it must cost only the saving."""

    async def _boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("database said no")

    monkeypatch.setattr(pipeline.queries, "reusable_snapshot", _boom)
    assert await _run(_selection([_file("a.py", "x = 1\n")])) == (set(), 0)


async def test_nothing_unchanged_skips_the_copy_entirely(fake) -> None:  # noqa: ANN001
    q = fake(FakeQueries(prior=PRIOR, digests={"old.py": _digest("gone\n")}))
    assert await _run(_selection([_file("new.py", "fresh\n")])) == (set(), 0)
    assert q.copied == []
