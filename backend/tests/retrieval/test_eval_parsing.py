"""Unit tests for eval.py's EVAL.md parsing — no DB, no models."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_EVAL_PY = Path(__file__).resolve().parents[2] / "scripts" / "eval.py"


def _load_eval_module() -> object:
    spec = importlib.util.spec_from_file_location("eval_script", _EVAL_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_extract_yaml_block_pulls_first_fence() -> None:
    ev = _load_eval_module()
    text = "intro\n```yaml\n- id: q01\n  question: hi\n```\ntrailer\n"
    assert ev._extract_yaml_block(text) == "- id: q01\n  question: hi"


def test_parse_eval_md_reads_frozen_ground_truth() -> None:
    ev = _load_eval_module()
    url, sha, questions = ev._parse_eval_md()
    assert url == "https://github.com/encode/httpx"
    assert sha == "b5addb64f0161ff6bfe94c124ef76f6a1fba5254"
    assert len(questions) == 20
    q01 = next(q for q in questions if q["id"] == "q01")
    assert q01["truth"]["files"] == ["httpx/_config.py"]
    assert "Timeout" in q01["truth"]["symbols"]


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
