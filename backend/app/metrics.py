"""A small Prometheus-format metrics registry, written rather than installed.

`prometheus-client` would be the obvious dependency here, and CLAUDE.md rule 11
says ask before adding one. This is what the application actually needs — three
metric types and a text renderer, about 150 lines — so it is written out instead
of pulled in. Swapping to the real library later is a mechanical change: the
`inc` / `set` / `observe` / `time` surface is deliberately the same.

Everything is thread-safe (a lock per metric) because observations arrive from
both the event loop and the inference thread pool.

Exposition format (`render()`), per the Prometheus text spec::

    # HELP http_requests_total Requests handled.
    # TYPE http_requests_total counter
    http_requests_total{method="GET",path="/repos",status="200"} 3

Histograms render the cumulative `_bucket`/`_sum`/`_count` triple, which is what
makes `histogram_quantile` and `rate()` work on the scraping side.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager

# A metric sample's labels, normalized to a sorted tuple so that
# `{a=1, b=2}` and `{b=2, a=1}` are one series and not two.
LabelKey = tuple[tuple[str, str], ...]

# Latency buckets in seconds. Spread wide on purpose: this application serves
# both sub-millisecond row lookups and three-minute agent runs, and a bucket set
# that only covers web-request latencies would put every chat in `+Inf`.
LATENCY_BUCKETS: tuple[float, ...] = (
    0.005, 0.025, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0,
)
# Waits on a connection pool are either ~0 or a symptom. Fine resolution low
# down, so "the pool is saturated" shows up before it becomes "the API is down".
POOL_WAIT_BUCKETS: tuple[float, ...] = (
    0.0005, 0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 30.0,
)
COUNT_BUCKETS: tuple[float, ...] = (0, 1, 2, 3, 4, 5, 6, 7, 8)


def _escape(value: str) -> str:
    """Escape a label value per the exposition format."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _render_labels(key: LabelKey, extra: tuple[str, str] | None = None) -> str:
    pairs = list(key) + ([extra] if extra else [])
    if not pairs:
        return ""
    body = ",".join(f'{name}="{_escape(value)}"' for name, value in pairs)
    return "{" + body + "}"


class _Metric:
    """Shared naming, labelling, and locking for the three metric types."""

    kind = "untyped"

    def __init__(self, name: str, help_text: str, labelnames: Sequence[str] = ()) -> None:
        self.name = name
        self.help_text = help_text
        self.labelnames = tuple(labelnames)
        self._lock = threading.Lock()
        REGISTRY.append(self)

    def _key(self, labels: dict[str, str]) -> LabelKey:
        """Normalize a label dict, defaulting anything the caller omitted.

        A missing label becomes ``""`` rather than an error: a metric call is
        never worth crashing a request over.
        """
        return tuple((name, str(labels.get(name, ""))) for name in self.labelnames)

    def _header(self) -> list[str]:
        return [
            f"# HELP {self.name} {self.help_text}",
            f"# TYPE {self.name} {self.kind}",
        ]

    def render(self) -> list[str]:  # pragma: no cover — overridden
        raise NotImplementedError


class Counter(_Metric):
    """Monotonically increasing count."""

    kind = "counter"

    def __init__(self, name: str, help_text: str, labelnames: Sequence[str] = ()) -> None:
        super().__init__(name, help_text, labelnames)
        self._values: dict[LabelKey, float] = {}

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        key = self._key(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def render(self) -> list[str]:
        with self._lock:
            items = sorted(self._values.items())
        return self._header() + [
            f"{self.name}{_render_labels(key)} {value!r}" for key, value in items
        ]


class Gauge(_Metric):
    """A value that goes up and down; set at observation or scrape time."""

    kind = "gauge"

    def __init__(self, name: str, help_text: str, labelnames: Sequence[str] = ()) -> None:
        super().__init__(name, help_text, labelnames)
        self._values: dict[LabelKey, float] = {}

    def set(self, value: float, **labels: str) -> None:
        key = self._key(labels)
        with self._lock:
            self._values[key] = float(value)

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        key = self._key(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def render(self) -> list[str]:
        with self._lock:
            items = sorted(self._values.items())
        return self._header() + [
            f"{self.name}{_render_labels(key)} {value!r}" for key, value in items
        ]


class Histogram(_Metric):
    """Cumulative bucket counts plus sum and count, per label set."""

    kind = "histogram"

    def __init__(
        self,
        name: str,
        help_text: str,
        labelnames: Sequence[str] = (),
        buckets: Sequence[float] = LATENCY_BUCKETS,
    ) -> None:
        super().__init__(name, help_text, labelnames)
        self.buckets = tuple(sorted(buckets))
        self._counts: dict[LabelKey, list[int]] = {}
        self._sums: dict[LabelKey, float] = {}

    def observe(self, value: float, **labels: str) -> None:
        key = self._key(labels)
        with self._lock:
            counts = self._counts.setdefault(key, [0] * (len(self.buckets) + 1))
            self._sums[key] = self._sums.get(key, 0.0) + value
            # Buckets are cumulative: an observation lands in its own bucket and
            # every wider one, which is what `le=` means to a scraper.
            for i, edge in enumerate(self.buckets):
                if value <= edge:
                    counts[i] += 1
            counts[-1] += 1  # +Inf

    @contextmanager
    def time(self, **labels: str) -> Iterator[None]:
        """Time the enclosed block, recording even when it raises."""
        started = time.perf_counter()
        try:
            yield
        finally:
            self.observe(time.perf_counter() - started, **labels)

    def render(self) -> list[str]:
        with self._lock:
            items = sorted(self._counts.items())
            sums = dict(self._sums)
        lines = self._header()
        for key, counts in items:
            for edge, count in zip(self.buckets, counts, strict=False):
                lines.append(
                    f"{self.name}_bucket{_render_labels(key, ('le', repr(edge)))} {count}"
                )
            lines.append(
                f"{self.name}_bucket{_render_labels(key, ('le', '+Inf'))} {counts[-1]}"
            )
            lines.append(f"{self.name}_sum{_render_labels(key)} {sums.get(key, 0.0)!r}")
            lines.append(f"{self.name}_count{_render_labels(key)} {counts[-1]}")
        return lines


REGISTRY: list[_Metric] = []


def render() -> str:
    """The whole registry in Prometheus text exposition format."""
    lines: list[str] = []
    for metric in REGISTRY:
        lines.extend(metric.render())
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# The application's metrics. Defined once, here, so the names stay greppable.
# ---------------------------------------------------------------------------

# `path` is always the *route template* (`/repos/{repo_id}`), never the raw URL:
# labelling by raw path would mint a new time series per repo id and eventually
# take the scrape target down with it.
http_requests = Counter(
    "http_requests_total", "HTTP requests handled.", ("method", "path", "status")
)
http_request_duration = Histogram(
    "http_request_duration_seconds", "HTTP request latency.", ("method", "path")
)
http_requests_in_flight = Gauge(
    "http_requests_in_flight", "HTTP requests currently being handled."
)

rate_limit_rejections = Counter(
    "rate_limit_rejections_total", "Requests refused by the rate limiter.", ("rule",)
)

# The metric that makes problem #1 visible before a user reports it: if this
# grows, connections are being held longer than the work needs them.
db_pool_acquire_wait = Histogram(
    "db_pool_acquire_wait_seconds",
    "Time spent waiting for a pooled connection.",
    buckets=POOL_WAIT_BUCKETS,
)
db_pool_size = Gauge("db_pool_size", "Connections currently held by the pool.")
db_pool_idle = Gauge("db_pool_idle", "Pooled connections currently idle.")
# Pooled connections the server had already dropped, caught on checkout rather
# than as a 500 in someone's request. A steady trickle is a managed database
# reaping idle connections and is fine; a spike is the database going away.
db_pool_dead_connections = Counter(
    "db_pool_dead_connections_total",
    "Pooled connections found dead on checkout and discarded.",
)

chat_streams = Counter(
    "chat_streams_total", "Chat streams by how they ended.", ("outcome",)
)
chat_duration = Histogram("chat_duration_seconds", "Wall-clock time of one agent run.")
chat_tool_calls = Histogram(
    "chat_tool_calls", "Tool calls executed per answer.", buckets=COUNT_BUCKETS
)
chat_streams_active = Gauge("chat_streams_active", "Chat streams currently open.")

inference_duration = Histogram(
    "inference_duration_seconds", "Model forward-pass time.", ("op",)
)
inference_queue_depth = Gauge(
    "inference_queue_depth", "Inference jobs submitted and not yet finished."
)
