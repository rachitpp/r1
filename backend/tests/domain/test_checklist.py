"""The §22 onboarding checklist builder.

`build_checklist` is pure, so these are plain function calls with no database
and no app. What is worth pinning is the *editorial* behaviour — the order, and
the refusal to pad — because that is the part a future change would break
silently while every field still typechecked.
"""

from __future__ import annotations

from app.domain.checklist import CHECKLIST_MAX_ITEMS, STEP_MAX_LINES, build_checklist

N_LINES = {"pkg/__main__.py": 30, "pkg/core.py": 200, "pkg/api.py": 80}

ENTRY = [{"path": "pkg/__main__.py"}]
MODULES = [{"path": "pkg/core.py", "n_symbols": 9, "fan_in": 7, "fan_out": 1}]
KEY = [
    {
        "name": "Client",
        "qualname": "pkg.core.Client",
        "kind": "class",
        "file_path": "pkg/core.py",
        "start_line": 20,
        "end_line": 90,
        "refs": 12,
    }
]
API = [
    {"name": "connect", "file_path": "pkg/api.py", "start_line": 5, "end_line": 9},
    {"name": "close", "file_path": "pkg/api.py", "start_line": 11, "end_line": 14},
]
TESTED = [{"file_path": "pkg/core.py", "n_tests": 23, "start_line": 1}]


def _full(**over):
    kwargs = {
        "entry_points": ENTRY,
        "modules": MODULES,
        "key_symbols": KEY,
        "api_symbols": API,
        "tested_files": TESTED,
        "n_lines_of": N_LINES,
    }
    kwargs.update(over)
    return build_checklist(**kwargs)


def test_produces_five_steps_in_reading_order() -> None:
    """Narrative order, not ranked order — the kinds are the assertion."""
    assert [i.kind for i in _full()] == [
        "entry_point",
        "hub",
        "key_symbol",
        "public_api",
        "most_tested",
    ]


def test_never_exceeds_the_promised_length() -> None:
    assert len(_full()) <= CHECKLIST_MAX_ITEMS


def test_every_step_carries_a_citable_range() -> None:
    """A step you cannot open is advice, not a checklist."""
    for item in _full():
        assert item.file_path
        assert item.start_line >= 1
        assert item.end_line >= item.start_line


def test_every_step_carries_a_question_to_ask() -> None:
    """The `?q=` payload is the whole "launch-point into chat" idea."""
    for item in _full():
        assert item.question.endswith("?")
        assert len(item.question) > 20


def test_a_library_with_no_entry_point_simply_has_four_steps() -> None:
    """Absent, not padded, and the rest keep their order."""
    items = _full(entry_points=[])
    assert [i.kind for i in items] == [
        "hub",
        "key_symbol",
        "public_api",
        "most_tested",
    ]


def test_a_repo_with_no_resolved_test_edges_drops_the_test_step() -> None:
    assert "most_tested" not in {i.kind for i in _full(tested_files=[])}


def test_an_empty_graph_yields_an_empty_checklist() -> None:
    """Nothing to say is said by saying nothing."""
    assert (
        build_checklist(
            entry_points=[],
            modules=[],
            key_symbols=[],
            api_symbols=[],
            tested_files=[],
            n_lines_of={},
        )
        == []
    )


def test_a_module_nothing_imports_is_not_called_a_hub() -> None:
    """fan_in 0 means nothing depends on it, whatever its rank in the list."""
    orphan = [{"path": "pkg/lonely.py", "n_symbols": 2, "fan_in": 0, "fan_out": 3}]
    assert "hub" not in {i.kind for i in _full(modules=orphan)}


def test_the_hub_step_reports_its_real_fan_in() -> None:
    hub = next(i for i in _full() if i.kind == "hub")
    assert "7 other modules" in hub.detail
    assert hub.file_path == "pkg/core.py"


def test_singular_and_plural_are_both_written_correctly() -> None:
    """Cosmetic, and the kind of thing that survives to a screenshot."""
    one = [{"path": "pkg/core.py", "n_symbols": 9, "fan_in": 1, "fan_out": 1}]
    hub = next(i for i in _full(modules=one) if i.kind == "hub")
    assert "1 other module." in hub.detail

    one_test = [{"file_path": "pkg/core.py", "n_tests": 1, "start_line": 1}]
    t = next(i for i in _full(tested_files=one_test) if i.kind == "most_tested")
    assert "1 test " in t.detail


def test_the_key_symbol_step_names_the_symbol_not_the_file() -> None:
    key = next(i for i in _full() if i.kind == "key_symbol")
    assert "`Client`" in key.title
    assert "pkg.core.Client" in key.question
    # It points at the definition's own lines, not the top of the file...
    assert key.start_line == 20
    # ...capped to a screenful. `Client` runs to 90; a step is a pointer.
    assert key.end_line == 60


def test_the_api_step_lists_a_few_names_and_elides_the_rest() -> None:
    many = [
        {"name": f"f{i}", "file_path": "pkg/api.py", "start_line": i, "end_line": i}
        for i in range(1, 7)
    ]
    api = next(i for i in _full(api_symbols=many) if i.kind == "public_api")
    assert "`f1`, `f2`, `f3`, `f4` …" in api.detail
    # Four named, six available — the elision is what keeps it a line, not a list.
    assert "`f5`" not in api.detail


def test_ranges_are_clamped_to_the_file_length() -> None:
    """A 30-line file must not produce a citation to line 41."""
    entry = next(i for i in _full() if i.kind == "entry_point")
    assert entry.end_line <= N_LINES["pkg/__main__.py"]


def test_an_unknown_file_length_does_not_invent_a_range() -> None:
    """`n_lines_of` misses a path: the range collapses rather than guessing."""
    items = build_checklist(
        entry_points=[{"path": "pkg/ghost.py"}],
        modules=[],
        key_symbols=[],
        api_symbols=[],
        tested_files=[],
        n_lines_of={},
    )
    assert items[0].start_line == 1
    assert items[0].end_line == 1


# --- the public-surface step, which real output caught -----------------------


EXAMPLE_API = [
    # A demo app's `__all__` outranks the library's in a repo-wide scan — this
    # is flask's actual shape, and it put `examples/celery/...` in the step.
    {
        "name": "celery_init_app",
        "file_path": "examples/celery/src/task_app/__init__.py",
        "start_line": 29,
        "end_line": 39,
    },
    {
        "name": "create_app",
        "file_path": "examples/celery/src/task_app/__init__.py",
        "start_line": 40,
        "end_line": 50,
    },
    {
        "name": "create_app",
        "file_path": "examples/javascript/js_example/__init__.py",
        "start_line": 1,
        "end_line": 8,
    },
    {"name": "Flask", "file_path": "src/flask/__init__.py", "start_line": 1, "end_line": 4},
    {"name": "jsonify", "file_path": "src/flask/__init__.py", "start_line": 5, "end_line": 6},
]
FLASK_HUB = [{"path": "src/flask/helpers.py", "n_symbols": 20, "fan_in": 66, "fan_out": 4}]


def test_the_surface_step_prefers_the_hub_s_own_package() -> None:
    """An example app's `__all__` must not stand in for the library's."""
    api = next(
        i
        for i in _full(api_symbols=EXAMPLE_API, modules=FLASK_HUB)
        if i.kind == "public_api"
    )
    assert api.file_path.startswith("src/flask/")
    assert "examples/" not in api.file_path
    assert "`Flask`" in api.detail
    assert "celery_init_app" not in api.detail


def test_duplicate_exported_names_are_shown_once() -> None:
    """`create_app` appears twice in flask's scan; a surface that repeats reads as a bug."""
    api = next(
        i for i in _full(api_symbols=EXAMPLE_API, modules=[]) if i.kind == "public_api"
    )
    assert api.detail.count("create_app") == 1


def test_the_surface_falls_back_rather_than_vanishing() -> None:
    """No hub, or nothing inside its package: still name the real symbols."""
    outside = [
        {"name": "thing", "file_path": "scripts/tool.py", "start_line": 1, "end_line": 2}
    ]
    api = next(
        i
        for i in _full(api_symbols=outside, modules=FLASK_HUB)
        if i.kind == "public_api"
    )
    assert api.file_path == "scripts/tool.py"


def test_no_step_cites_more_than_a_screenful() -> None:
    """flask's `Flask` class is 1516 lines. A citation that size is not a pointer."""
    huge = [
        {
            "name": "Flask",
            "qualname": "flask.app.Flask",
            "kind": "class",
            "file_path": "pkg/core.py",
            "start_line": 109,
            "end_line": 1625,
            "refs": 40,
        }
    ]
    for item in _full(key_symbols=huge):
        assert item.end_line - item.start_line <= STEP_MAX_LINES


def test_the_package_s_own_dunder_init_wins_over_a_sibling_module() -> None:
    """Without this, httpx reported its public surface as `main` — the CLI entry."""
    api = [
        {"name": "main", "file_path": "httpx/_main.py", "start_line": 313, "end_line": 506},
        {"name": "Client", "file_path": "httpx/__init__.py", "start_line": 1, "end_line": 9},
    ]
    hub = [{"path": "httpx/_exceptions.py", "n_symbols": 30, "fan_in": 80, "fan_out": 2}]
    step = next(
        i for i in _full(api_symbols=api, modules=hub) if i.kind == "public_api"
    )
    assert step.file_path == "httpx/__init__.py"
    assert "`Client`" in step.detail
    assert "`main`" not in step.detail
