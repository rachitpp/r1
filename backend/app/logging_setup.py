"""One logging setup for all three entrypoints (API, worker, CLIs).

Exists because of a Phase 6 onboarding finding: a first run printed ~30 lines
of ``INFO httpx: HTTP Request: HEAD https://huggingface.co/...`` before anything
about this application appeared. A newcomer cannot tell that from a failure
loop, and it buries the one line that matters ("embedder warm", "worker up").

Model downloads are still visible — `huggingface_hub` logs those on its own
logger at INFO, and a silent 130 MB download is worse than a noisy one. What is
silenced is the per-file HTTP chatter underneath it.

**Request correlation.** Every log line carries a ``request_id``. It is held in
a :class:`~contextvars.ContextVar`, which is the only mechanism that survives
the trip through FastAPI's dependency graph, an ``await`` on a provider, and a
``logger.exception`` three modules deep — all without threading an id parameter
through function signatures that have no other reason to know about HTTP. The
API sets it per request (``app/api/middleware.py``); everywhere else it reads
``-``, which is honest: a worker job is not a request.
"""

from __future__ import annotations

import json
import logging
from contextvars import ContextVar

FORMAT = "%(levelname)s %(name)s [%(request_id)s]: %(message)s"

# Third-party loggers that are chatty at INFO and say nothing an operator of
# this application needs. `httpx` is the loud one: huggingface_hub makes one
# HEAD request per model file and logs every redirect.
_NOISY = ("httpx", "httpcore", "urllib3", "filelock")

# "-" rather than "" so a text log line reads `[-]` instead of `[]` — visibly
# "no request", not a formatting bug.
NO_REQUEST = "-"

_request_id: ContextVar[str] = ContextVar("request_id", default=NO_REQUEST)


def set_request_id(value: str) -> None:
    """Bind ``value`` as the current context's request id."""
    _request_id.set(value)


def get_request_id() -> str:
    """The current context's request id, or ``"-"`` outside a request."""
    return _request_id.get()


class RequestIdFilter(logging.Filter):
    """Attach the current request id to every record.

    A filter rather than a custom Logger subclass, because it has to cover
    records emitted by uvicorn, langchain, and asyncpg as well as ours — and
    those loggers are not ours to replace.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line, for anything that ships logs somewhere."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", NO_REQUEST),
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: int | None = None, *, fmt: str | None = None) -> None:
    """Configure root logging and quiet third-party request chatter.

    ``level``/``fmt`` default to ``LOG_LEVEL``/``LOG_FORMAT`` from the
    environment. Settings are read defensively: a CLI that cannot load ``.env``
    should still get logging, and discovering that through a stack trace about
    ``DATABASE_URL`` would be a poor introduction.

    ``force=True`` makes a second call replace the first rather than silently do
    nothing, which is what ``basicConfig`` would otherwise do once uvicorn or a
    CLI has already installed a handler.
    """
    if level is None or fmt is None:
        try:
            from app.config import get_settings

            settings = get_settings()
            level = level if level is not None else logging.getLevelNamesMapping().get(
                settings.LOG_LEVEL.upper(), logging.INFO
            )
            fmt = fmt if fmt is not None else settings.LOG_FORMAT
        except Exception:  # noqa: BLE001 — logging must never be the thing that fails
            level = level if level is not None else logging.INFO
            fmt = fmt or "text"

    logging.basicConfig(level=level, format=FORMAT, force=True)
    formatter: logging.Formatter = (
        JsonFormatter() if fmt.lower() == "json" else logging.Formatter(FORMAT)
    )
    for handler in logging.getLogger().handlers:
        handler.setFormatter(formatter)
        handler.addFilter(RequestIdFilter())

    for name in _NOISY:
        logging.getLogger(name).setLevel(logging.WARNING)
