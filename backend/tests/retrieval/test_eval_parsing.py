"""Unit tests for eval.py's EVAL.md parsing — no DB, no models."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_EVAL_PY = Path(__file__).resolve().parents[2] / "scripts" / "eval.py"


def _load_eval_module() -> object:
    """Load scripts/eval.py as a module.

    The module must be registered in ``sys.modules`` *before* ``exec_module``:
    that is the documented importlib recipe, and anything resolving a class back
    to its module — ``dataclasses``, ``typing.get_type_hints``, pickle — fails
    with ``AttributeError: 'NoneType' object has no attribute '__dict__'``
    without it.
    """
    spec = importlib.util.spec_from_file_location("eval_script", _EVAL_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        del sys.modules[spec.name]
        raise
    return module


_DOCS = Path(__file__).resolve().parents[3] / "docs"


def test_extract_yaml_block_pulls_first_fence() -> None:
    ev = _load_eval_module()
    text = "intro\n```yaml\n- id: q01\n  question: hi\n```\ntrailer\n"
    assert ev._extract_yaml_block(text, Path("x.md")) == "- id: q01\n  question: hi"


def test_parse_eval_md_reads_frozen_ground_truth() -> None:
    ev = _load_eval_module()
    url, sha, questions = ev._parse_eval_md(_DOCS / "EVAL.md")
    assert url == "https://github.com/encode/httpx"
    assert sha == "b5addb64f0161ff6bfe94c124ef76f6a1fba5254"
    assert len(questions) == 20
    q01 = next(q for q in questions if q["id"] == "q01")
    assert q01["truth"]["files"] == ["httpx/_config.py"]
    assert "Timeout" in q01["truth"]["symbols"]


def test_a_second_benchmark_parses_independently() -> None:
    """The replication benchmark is a peer of EVAL.md, not a variant of it.

    Same parser, different file, different repo and pin — which is what lets a
    second repo be measured without the first one's frozen ground truth being
    touched or its results file appended to.
    """
    ev = _load_eval_module()
    url, sha, questions = ev._parse_eval_md(_DOCS / "EVAL-FLASK.md")
    assert url == "https://github.com/pallets/flask"
    assert sha == "6a2f545bfd8ed31e19066a299296917e034aca58"
    assert len(questions) == 20
    assert [q["id"] for q in questions] == [f"q{i:02d}" for i in range(1, 21)]


def test_both_benchmarks_share_the_same_ground_truth_shape() -> None:
    """A second benchmark is only comparable if it is the same kind of object."""
    ev = _load_eval_module()
    for name in ("EVAL.md", "EVAL-FLASK.md"):
        _, _, questions = ev._parse_eval_md(_DOCS / name)
        assert len(questions) == 20, name
        for q in questions:
            assert q["tier"] in {"locate", "conceptual", "flow"}, (name, q["id"])
            assert q["truth"]["files"], (name, q["id"])
            assert isinstance(q["question"], str) and q["question"].strip()


def test_a_missing_benchmark_file_fails_loudly() -> None:
    ev = _load_eval_module()
    with pytest.raises(SystemExit, match="benchmark file not found"):
        ev._parse_eval_md(_DOCS / "EVAL-DOES-NOT-EXIST.md")


def _hit(file_path: str = "x.py", symbol: str | None = None) -> dict:
    return {"file_path": file_path, "symbol": symbol}


def test_first_hit_rank_file_and_symbol_positions() -> None:
    ev = _load_eval_module()
    hits = [_hit("a.py"), _hit("httpx/_config.py"), _hit("c.py")]
    # 2nd hit is the truth file -> rank 2.
    assert ev._first_hit_rank(hits, {"httpx/_config.py"}, []) == 2
    # symbol suffix match on the 1st hit -> rank 1.
    hits2 = [_hit("a.py", "httpx._config.Timeout"), _hit("b.py")]
    assert ev._first_hit_rank(hits2, set(), ["Timeout"]) == 1


def test_first_hit_rank_returns_none_on_miss() -> None:
    ev = _load_eval_module()
    hits = [_hit("a.py", "pkg.foo"), _hit("b.py", "pkg.bar")]
    assert ev._first_hit_rank(hits, {"z.py"}, ["baz"]) is None


def test_rank_drives_hit_at_k_and_reciprocal_rank() -> None:
    # The metric contract eval.run() relies on: hit@k iff rank<=k; RR = 1/rank.
    ev = _load_eval_module()
    hits = [_hit("a.py"), _hit("b.py"), _hit("c.py"), _hit("truth.py")]
    rank = ev._first_hit_rank(hits, {"truth.py"}, [])
    assert rank == 4
    assert (rank <= 3, rank <= 5, rank <= 10) == (False, True, True)
    assert 1.0 / rank == 0.25
