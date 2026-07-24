"""Parser tests: the ROADMAP chunking cases plus module-path derivation."""

from __future__ import annotations

import pytest

from app.ingest.filters import SourceFile
from app.ingest.parser import (
    KIND_CLASS,
    KIND_FUNCTION,
    KIND_METHOD,
    KIND_MODULE,
    module_path_from_rel,
    parse_file,
)

from .conftest import parse_source


def _by_symbol(parsed) -> dict[str, object]:
    return {c.symbol: c for c in parsed.chunks}


def test_top_level_function() -> None:
    parsed = parse_source("pkg/m.py", "def greet(name):\n    return name\n")
    chunk = _by_symbol(parsed)["pkg.m.greet"]
    assert chunk.kind == KIND_FUNCTION
    assert chunk.signature == "def greet(name)"
    assert chunk.start_line == 1
    assert chunk.end_line == 2
    assert "return name" in chunk.code


def test_method_gets_full_qualname() -> None:
    src = "class A:\n    def m(self, x):\n        return x\n"
    parsed = parse_source("pkg/m.py", src)
    chunk = _by_symbol(parsed)["pkg.m.A.m"]
    assert chunk.kind == KIND_METHOD
    assert chunk.signature == "def m(self, x)"
    # Method code is dedented to column 0.
    assert chunk.code.startswith("def m(self, x):")


def test_nested_function_stays_inside_parent() -> None:
    src = (
        "def outer():\n"
        "    def inner():\n"
        "        return 1\n"
        "    return inner()\n"
    )
    parsed = parse_source("m.py", src)
    symbols = {c.symbol for c in parsed.chunks}
    assert symbols == {"m.outer"}  # no separate chunk for inner
    outer = _by_symbol(parsed)["m.outer"]
    assert "def inner():" in outer.code


def test_decorated_function_start_line_is_first_decorator() -> None:
    src = (
        "import x\n"
        "\n"
        "@app.route('/')\n"
        "@login_required\n"
        "def handler():\n"
        "    return 1\n"
    )
    parsed = parse_source("m.py", src)
    chunk = _by_symbol(parsed)["m.handler"]
    assert chunk.start_line == 3  # first decorator (1-based)
    assert chunk.end_line == 6
    assert chunk.code.startswith("@app.route('/')")
    assert chunk.signature == "def handler()"  # decorators excluded from sig


def test_async_function() -> None:
    parsed = parse_source("m.py", "async def go():\n    await x()\n")
    chunk = _by_symbol(parsed)["m.go"]
    assert chunk.signature == "async def go()"
    assert chunk.kind == KIND_FUNCTION


def test_class_skeleton_correctness() -> None:
    src = (
        "class Service(Base):\n"
        '    """Service docstring."""\n'
        "    timeout: int = 30\n"
        "\n"
        "    def start(self):\n"
        "        self._run()\n"
        "        return True\n"
    )
    parsed = parse_source("pkg/svc.py", src)
    skeleton = _by_symbol(parsed)["pkg.svc.Service"]
    assert skeleton.kind == KIND_CLASS
    assert skeleton.signature == "class Service(Base)"
    # Docstring and class attribute kept; method body elided to "...".
    assert '"""Service docstring."""' in skeleton.code
    assert "timeout: int = 30" in skeleton.code
    assert "def start(self): ..." in skeleton.code
    assert "self._run()" not in skeleton.code
    # Method still emitted as its own chunk with full body.
    method = _by_symbol(parsed)["pkg.svc.Service.start"]
    assert method.kind == KIND_METHOD
    assert "self._run()" in method.code


def test_module_chunk_docstring_imports_assignments() -> None:
    src = (
        '"""Top module."""\n'
        "import os\n"
        "from a import b\n"
        "\n"
        "VERSION = '1.0'\n"
        "\n"
        "def f():\n"
        "    return VERSION\n"
    )
    parsed = parse_source("pkg/__init__.py", src)
    module = _by_symbol(parsed)["pkg"]
    assert module.kind == KIND_MODULE
    assert module.signature is None
    assert module.start_line == 1
    assert module.end_line == 5  # last assignment, not the def
    assert "VERSION = '1.0'" in module.code
    assert "def f" not in module.code
    assert parsed.imports == ["import os", "from a import b"]


def test_syntax_error_file_skipped() -> None:
    bad = SourceFile(path="bad.py", text="def f(:\n    pass\n", n_lines=2)
    assert parse_file(bad) is None


def test_empty_file_no_chunks_no_crash() -> None:
    empty = parse_file(SourceFile(path="empty.py", text="", n_lines=0))
    assert empty is not None
    assert empty.chunks == []


def test_comment_only_file_has_no_module_chunk() -> None:
    parsed = parse_source("c.py", "# only a comment\n")
    assert parsed.chunks == []


@pytest.mark.parametrize(
    "rel,expected",
    [
        ("a/b/c.py", "a.b.c"),
        ("a/b/__init__.py", "a.b"),
        ("c.py", "c"),
        ("__init__.py", ""),
    ],
)
def test_module_path_derivation(rel: str, expected: str) -> None:
    assert module_path_from_rel(rel) == expected
