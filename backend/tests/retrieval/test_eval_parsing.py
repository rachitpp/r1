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
