"""One logging setup for all three entrypoints (API, worker, CLIs).

Exists because of a Phase 6 onboarding finding: a first run printed ~30 lines
of ``INFO httpx: HTTP Request: HEAD https://huggingface.co/...`` before anything
about this application appeared. A newcomer cannot tell that from a failure
loop, and it buries the one line that matters ("embedder warm", "worker up").

Model downloads are still visible — `huggingface_hub` logs those on its own
logger at INFO, and a silent 130 MB download is worse than a noisy one. What is
silenced is the per-file HTTP chatter underneath it.
"""

from __future__ import annotations

import logging

FORMAT = "%(levelname)s %(name)s: %(message)s"

# Third-party loggers that are chatty at INFO and say nothing an operator of
# this application needs. `httpx` is the loud one: huggingface_hub makes one
# HEAD request per model file and logs every redirect.
_NOISY = ("httpx", "httpcore", "urllib3", "filelock")


def configure_logging(level: int = logging.INFO) -> None:
    """Configure root logging and quiet third-party request chatter."""
    logging.basicConfig(level=level, format=FORMAT)
    for name in _NOISY:
        logging.getLogger(name).setLevel(logging.WARNING)
