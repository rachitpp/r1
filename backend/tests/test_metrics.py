"""The hand-rolled registry has to emit something Prometheus can actually parse.

Written out rather than installed (CLAUDE.md rule 11), which means the
exposition format is ours to get right: cumulative buckets, the `_sum`/`_count`
pair, escaped label values, and no series minted per raw URL.
"""

from __future__ import annotations

from app import metrics
from app.metrics import Counter, Gauge, Histogram, render


def test_counter_accumulates_per_label_set() -> None:
    c = Counter("t_counter_total", "help.", ("path",))
    c.inc(path="/a")
    c.inc(path="/a")
    c.inc(path="/b")
    out = "\n".join(c.render())
    assert 't_counter_total{path="/a"} 2.0' in out
    assert 't_counter_total{path="/b"} 1.0' in out


def test_counter_header_declares_type_and_help() -> None:
    c = Counter("t_typed_total", "how many.", ())
    c.inc()
    out = "\n".join(c.render())
    assert "# HELP t_typed_total how many." in out
    assert "# TYPE t_typed_total counter" in out


def test_gauge_sets_rather_than_accumulates() -> None:
    g = Gauge("t_gauge", "help.")
    g.set(5)
    g.set(2)
    assert "t_gauge 2.0" in "\n".join(g.render())


def test_histogram_buckets_are_cumulative() -> None:
    h = Histogram("t_hist", "help.", (), buckets=(1.0, 10.0))
    h.observe(0.5)
    h.observe(5.0)
    h.observe(50.0)
    out = "\n".join(h.render())
    # 0.5 only; 0.5 and 5.0; all three.
    assert 't_hist_bucket{le="1.0"} 1' in out
    assert 't_hist_bucket{le="10.0"} 2' in out
    assert 't_hist_bucket{le="+Inf"} 3' in out
    assert "t_hist_count 3" in out
    assert "t_hist_sum 55.5" in out


def test_histogram_records_even_when_the_timed_block_raises() -> None:
    """A latency metric that only counts successes hides every outage."""
    h = Histogram("t_timed", "help.", (), buckets=(1.0,))
    try:
        with h.time():
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert "t_timed_count 1" in "\n".join(h.render())


def test_label_values_are_escaped() -> None:
    c = Counter("t_escaped_total", "help.", ("q",))
    c.inc(q='a"b\\c')
    assert 't_escaped_total{q="a\\"b\\\\c"} 1.0' in "\n".join(c.render())


def test_missing_labels_default_rather_than_raise() -> None:
    """A metric call must never be the thing that fails a request."""
    c = Counter("t_partial_total", "help.", ("a", "b"))
    c.inc(a="x")
    assert 't_partial_total{a="x",b=""} 1.0' in "\n".join(c.render())


def test_render_includes_the_application_metrics() -> None:
    metrics.http_requests.inc(method="GET", path="/health", status="200")
    out = render()
    assert "# TYPE http_requests_total counter" in out
    assert "# TYPE http_request_duration_seconds histogram" in out
    assert "# TYPE db_pool_acquire_wait_seconds histogram" in out
    assert out.endswith("\n")
