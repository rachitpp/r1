"""The API must not import torch (SPEC §16.1, V3 done-when).

Every API replica carrying a 130 MB model (2.4 GB with the reranker) is the
reason §16 exists: HTTP capacity cannot be scaled independently of embedding
capacity while they live in one process.

CLAUDE.md rule 3 confines sentence-transformers to `app/ingest/embedder.py`, and
that module defers the import into its *constructors* — so importing the API
touches the seam without paying for it. That is a property no reviewer can hold
in their head across 48 files, which is why it is a test.

Run in a **subprocess**: by the time pytest reaches this file, other tests have
long since loaded torch into `sys.modules`, so an in-process check would pass
whatever the API did.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent

PROBE = """
import sys
import {module}
heavy = sorted(
    name for name in sys.modules
    if name == "torch" or name.startswith("torch.")
    or name == "sentence_transformers" or name.startswith("sentence_transformers.")
    or name == "transformers" or name.startswith("transformers.")
)
print(",".join(heavy[:8]))
"""


def _heavy_modules_after_importing(module: str) -> list[str]:
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(PROBE.format(module=module))],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    out = result.stdout.strip()
    return [m for m in out.split(",") if m]


def test_importing_the_api_does_not_load_torch() -> None:
    """`app.main` is what uvicorn imports on every API replica."""
    assert _heavy_modules_after_importing("app.main") == []


def test_importing_retrieval_does_not_load_torch() -> None:
    """`retrieval` imports the embedder factories, never the library (rule 3).

    This is the module most likely to regress: it is the one place that both
    serves requests and talks about embeddings.
    """
    assert _heavy_modules_after_importing("app.retrieval.hybrid") == []


def test_importing_the_embedder_module_itself_does_not_load_torch() -> None:
    """The deferral lives in the constructors, not at module scope.

    If this fails, every module in the import graph above it fails too — so it
    is the one to read first when the tests above go red.
    """
    assert _heavy_modules_after_importing("app.ingest.embedder") == []
