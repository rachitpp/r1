"""Serving behaviour under load and abuse (DECISIONS 2026-07-28).

End to end through the real middleware stack, the real error envelope, and the
real routes — only the connection, the queue, and the model are fakes, as
everywhere else in these API tests. What is being pinned here is what the
application does when a request is *not* the happy path: too many of them, too
big, too fast, or arriving at a process that cannot serve them.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from app.api import routes
from app.config import MAX_REQUEST_BYTES, QUESTION_MAX_CHARS, get_settings
from tests.api.fakes import FILE_CONTENT, FILE_PATH, REPO_ID, FakeArq, FakeConn

# ---------------------------------------------------------------------------
# Request correlation (#6)
# ---------------------------------------------------------------------------


async def test_every_response_carries_a_request_id(client: httpx.AsyncClient) -> None:
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.headers["x-request-id"]


async def test_each_request_gets_a_distinct_id(client: httpx.AsyncClient) -> None:
    first = await client.get("/health")
    second = await client.get("/health")
    assert first.headers["x-request-id"] != second.headers["x-request-id"]


async def test_a_client_supplied_request_id_is_not_trusted(
    client: httpx.AsyncClient,
) -> None:
    """TRUST_PROXY_HEADERS is off by default, so this header is attacker input."""
    resp = await client.get("/health", headers={"X-Request-ID": "spoofed"})
    assert resp.headers["x-request-id"] != "spoofed"


# ---------------------------------------------------------------------------
# Readiness (#6)
# ---------------------------------------------------------------------------


async def test_health_is_liveness_only(client: httpx.AsyncClient) -> None:
    """It must answer without touching a dependency, or an outage restarts us."""
    assert (await client.get("/health")).json() == {"ok": True}


async def test_ready_reports_each_dependency(client: httpx.AsyncClient) -> None:
    resp = await client.get("/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert set(body["checks"]) == {"postgres", "redis", "embedder"}
    assert body["checks"]["postgres"]["ok"] is True


async def test_ready_is_503_when_postgres_is_gone(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point: a process that cannot serve must not claim it can."""
    from app.main import app

    monkeypatch.setattr(app.state, "pool", None)
    resp = await client.get("/ready")
    assert resp.status_code == 503
    assert resp.json()["ok"] is False
    assert resp.json()["checks"]["postgres"]["ok"] is False


async def test_health_still_answers_when_ready_does_not(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.main import app

    monkeypatch.setattr(app.state, "pool", None)
    assert (await client.get("/health")).status_code == 200


# ---------------------------------------------------------------------------
# Metrics (#6)
# ---------------------------------------------------------------------------


async def test_metrics_expose_prometheus_text(client: httpx.AsyncClient) -> None:
    await client.get("/health")
    resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "# TYPE http_requests_total counter" in resp.text
    assert "# TYPE db_pool_acquire_wait_seconds histogram" in resp.text


async def test_metrics_label_paths_by_route_template_not_by_id(
    client: httpx.AsyncClient,
) -> None:
    """A label per repo id would mint a time series per repo, forever.

    The template is `{snapshot_id}` since §14: the path parameter was renamed
    with everything else, and the metric label follows the route. That renames
    one Prometheus series — harmless here, and the new name is the accurate one,
    but it is a real change for anything already graphing the old label.
    """
    await client.get(f"/repos/{REPO_ID}")
    text = (await client.get("/metrics")).text
    assert 'path="/repos/{snapshot_id}"' in text
    assert str(REPO_ID) not in text


async def test_metrics_require_the_token_when_one_is_set(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "METRICS_TOKEN", "s3cret")
    assert (await client.get("/metrics")).status_code == 401
    resp = await client.get("/metrics", headers={"Authorization": "Bearer s3cret"})
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Input bounds and body size (#4)
# ---------------------------------------------------------------------------


async def test_an_oversized_question_is_rejected(client: httpx.AsyncClient) -> None:
    """This string is billed per token; unbounded here is unbounded spend."""
    resp = await client.post(
        f"/repos/{REPO_ID}/chat", json={"question": "x" * (QUESTION_MAX_CHARS + 1)}
    )
    assert resp.status_code == 422


async def test_a_question_at_the_limit_is_accepted(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        f"/repos/{REPO_ID}/chat", json={"question": "x" * QUESTION_MAX_CHARS}
    )
    assert resp.status_code == 200


async def test_an_oversized_body_is_413_before_it_is_parsed(
    client: httpx.AsyncClient,
) -> None:
    resp = await client.post(
        "/repos",
        content=json.dumps({"url": "x" * (MAX_REQUEST_BYTES + 100)}),
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 413
    assert resp.json()["request_id"]


async def test_an_oversized_chunked_body_is_also_refused(
    client: httpx.AsyncClient,
) -> None:
    """Omitting Content-Length must not be a way around the limit."""

    async def chunks() -> Any:
        for _ in range(4):
            yield b"x" * (MAX_REQUEST_BYTES // 2)

    resp = await client.post(
        "/repos", content=chunks(), headers={"content-type": "application/json"}
    )
    assert resp.status_code == 413


# ---------------------------------------------------------------------------
# Rate limiting (#4)
# ---------------------------------------------------------------------------


async def test_repeated_ingests_are_rate_limited(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    rate_limit_rules: Any,
) -> None:
    monkeypatch.setattr(get_settings(), "RATE_LIMIT_INGEST_PER_HOUR", 2)
    rate_limit_rules()

    body = {"url": "https://github.com/psf/requests"}
    assert (await client.post("/repos", json=body)).status_code in (200, 201)
    assert (await client.post("/repos", json=body)).status_code in (200, 201)

    resp = await client.post("/repos", json=body)
    assert resp.status_code == 429
    assert int(resp.headers["retry-after"]) > 0
    assert "rate limit exceeded for ingest" in resp.json()["detail"]


async def test_reads_are_not_limited_by_the_ingest_rule(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    rate_limit_rules: Any,
) -> None:
    monkeypatch.setattr(get_settings(), "RATE_LIMIT_INGEST_PER_HOUR", 1)
    rate_limit_rules()

    for _ in range(5):
        assert (await client.get("/repos")).status_code == 200


async def test_a_429_is_readable_by_a_browser(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    rate_limit_rules: Any,
) -> None:
    """CORS sits outside the limiter, or the frontend sees an opaque failure."""
    monkeypatch.setattr(get_settings(), "RATE_LIMIT_INGEST_PER_HOUR", 0)
    rate_limit_rules()

    resp = await client.post(
        "/repos",
        json={"url": "https://github.com/psf/requests"},
        headers={"Origin": "http://localhost:3000"},
    )
    assert resp.status_code == 429
    assert resp.headers["access-control-allow-origin"] == "http://localhost:3000"


# ---------------------------------------------------------------------------
# Ingest capacity (#4)
# ---------------------------------------------------------------------------


async def test_ingest_is_refused_when_too_many_are_already_active(
    client: httpx.AsyncClient, conn: FakeConn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ARQ will hold any number of jobs; the machine running them will not."""
    monkeypatch.setattr(get_settings(), "MAX_ACTIVE_INGESTS", 1)

    async def busy(sql: str, *args: Any) -> int:
        return 5 if "count(*) FROM repo_snapshots" in sql else 0

    monkeypatch.setattr(conn, "fetchval", busy)
    resp = await client.post("/repos", json={"url": "https://github.com/psf/requests"})
    assert resp.status_code == 429
    # Per-user wording since §15.5 — the limit is the caller's, not the box's.
    assert "you already have" in resp.json()["detail"]
    assert "queued or running" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# File caching and ranges (#5)
# ---------------------------------------------------------------------------


async def test_a_file_response_is_cacheable(client: httpx.AsyncClient) -> None:
    resp = await client.get(f"/repos/{REPO_ID}/files", params={"path": FILE_PATH})
    assert resp.status_code == 200
    assert resp.headers["etag"]
    assert "immutable" in resp.headers["cache-control"]


async def test_a_matching_etag_returns_304_with_no_body(
    client: httpx.AsyncClient,
) -> None:
    """Every citation click refetches a file that cannot have changed."""
    first = await client.get(f"/repos/{REPO_ID}/files", params={"path": FILE_PATH})
    second = await client.get(
        f"/repos/{REPO_ID}/files",
        params={"path": FILE_PATH},
        headers={"If-None-Match": first.headers["etag"]},
    )
    assert second.status_code == 304
    assert not second.content


async def test_a_stale_etag_returns_the_file(client: httpx.AsyncClient) -> None:
    resp = await client.get(
        f"/repos/{REPO_ID}/files",
        params={"path": FILE_PATH},
        headers={"If-None-Match": '"not-the-current-one"'},
    )
    assert resp.status_code == 200
    assert resp.json()["content"] == FILE_CONTENT


async def test_a_line_range_returns_only_those_lines(
    client: httpx.AsyncClient,
) -> None:
    resp = await client.get(
        f"/repos/{REPO_ID}/files",
        params={"path": FILE_PATH, "start_line": 2, "end_line": 2},
    )
    body = resp.json()
    assert body["content"] == FILE_CONTENT.splitlines(keepends=True)[1]
    assert (body["start_line"], body["end_line"]) == (2, 2)
    # n_lines still describes the whole file, so a viewer can say "of N".
    assert body["n_lines"] == 2


async def test_a_full_file_read_is_byte_identical(client: httpx.AsyncClient) -> None:
    """No range must mean no transformation — trailing newline included."""
    resp = await client.get(f"/repos/{REPO_ID}/files", params={"path": FILE_PATH})
    assert resp.json()["content"] == FILE_CONTENT


async def test_a_range_past_the_end_of_the_file_is_empty_not_an_error(
    client: httpx.AsyncClient,
) -> None:
    """A viewer scrolled past EOF asks for a window that is simply not there."""
    resp = await client.get(
        f"/repos/{REPO_ID}/files",
        params={"path": FILE_PATH, "start_line": 900, "end_line": 950},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["content"] == ""
    assert body["end_line"] < body["start_line"]


async def test_a_range_is_clipped_to_the_end_of_the_file(
    client: httpx.AsyncClient,
) -> None:
    resp = await client.get(
        f"/repos/{REPO_ID}/files",
        params={"path": FILE_PATH, "start_line": 1, "end_line": 900},
    )
    body = resp.json()
    assert body["content"] == FILE_CONTENT
    assert (body["start_line"], body["end_line"]) == (1, 2)


async def test_a_backwards_range_is_422(client: httpx.AsyncClient) -> None:
    resp = await client.get(
        f"/repos/{REPO_ID}/files",
        params={"path": FILE_PATH, "start_line": 9, "end_line": 2},
    )
    assert resp.status_code == 422


async def test_a_range_gets_its_own_etag(client: httpx.AsyncClient) -> None:
    """Or a cached window would be served in place of the whole file."""
    whole = await client.get(f"/repos/{REPO_ID}/files", params={"path": FILE_PATH})
    part = await client.get(
        f"/repos/{REPO_ID}/files", params={"path": FILE_PATH, "start_line": 2}
    )
    assert whole.headers["etag"] != part.headers["etag"]


# ---------------------------------------------------------------------------
# Chat concurrency and slot accounting (#1, #3)
# ---------------------------------------------------------------------------


async def test_chat_is_refused_when_every_slot_is_busy(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(routes.chat_slots, "limit", 0)
    resp = await client.post(f"/repos/{REPO_ID}/chat", json={"question": "hi"})
    assert resp.status_code == 429
    assert int(resp.headers["retry-after"]) > 0
    assert "answer slots are busy" in resp.json()["detail"]


async def test_a_completed_stream_gives_its_slot_back(
    client: httpx.AsyncClient,
) -> None:
    """A slot leaked per answer means a server that refuses everything by lunch."""
    before = routes.chat_slots.used
    async with client.stream(
        "POST", f"/repos/{REPO_ID}/chat", json={"question": "how are tokens verified?"}
    ) as resp:
        async for _ in resp.aiter_lines():
            pass
    assert routes.chat_slots.used == before


async def test_an_abandoned_stream_gives_its_slot_back(
    conn: FakeConn, scripted_model: Any
) -> None:
    """A closed tab must release the slot, not hold it until the process dies.

    Driven at the generator rather than through the client: httpx's ASGI
    transport does not deliver a client disconnect, so going through the HTTP
    layer here would pass without ever exercising the abandonment path.
    """
    from app.api.chat_stream import chat_event_stream

    released = []
    stream = chat_event_stream(
        scripted_model,
        conn,
        REPO_ID,
        "anything",
        on_finish=lambda: released.append(True),
    )

    first = await stream.__anext__()  # the `status` event; now mid-run
    assert json.loads(first["data"]) == {"state": "thinking"}

    await stream.aclose()  # the consumer walks away
    assert released == [True]


async def test_a_failed_stream_also_gives_its_slot_back(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api import chat_stream

    async def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(chat_stream, "repo_facts", boom)
    before = routes.chat_slots.used
    async with client.stream(
        "POST", f"/repos/{REPO_ID}/chat", json={"question": "anything"}
    ) as resp:
        async for _ in resp.aiter_lines():
            pass
    assert routes.chat_slots.used == before


# ---------------------------------------------------------------------------
# Error surface (#7)
# ---------------------------------------------------------------------------


async def _stream_events(
    client: httpx.AsyncClient, question: str = "anything"
) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    name: str | None = None
    async with client.stream(
        "POST", f"/repos/{REPO_ID}/chat", json={"question": question}
    ) as resp:
        async for line in resp.aiter_lines():
            if line.startswith("event:"):
                name = line.split(":", 1)[1].strip()
            elif line.startswith("data:") and name is not None:
                events.append((name, json.loads(line.split(":", 1)[1].strip())))
                name = None
    return events


async def test_a_stream_failure_does_not_leak_the_exception_text(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api import chat_stream

    async def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError(
            "connection to postgresql://app:hunter2@db:5432/r1 failed"
        )

    monkeypatch.setattr(chat_stream, "repo_facts", boom)
    errors = [data for name, data in await _stream_events(client) if name == "error"]
    assert len(errors) == 1
    assert "hunter2" not in errors[0]["message"]
    assert "db:5432" in errors[0]["message"]  # still diagnosable
    assert errors[0]["request_id"]


async def test_a_stream_timeout_ends_in_one_error_event(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The §7.2 tool cap bounds calls, not time; something has to bound time."""
    from app.api import chat_stream

    async def never(*_a: Any, **_k: Any) -> Any:
        await asyncio.sleep(30)

    monkeypatch.setattr(chat_stream, "repo_facts", never)
    monkeypatch.setattr(get_settings(), "CHAT_TIMEOUT_S", 0.05)

    events = await _stream_events(client)
    assert [name for name, _ in events] == ["status", "error"]
    assert "timed out" in events[-1][1]["message"]


async def test_an_unhandled_route_error_is_a_500_envelope(
    client: httpx.AsyncClient, conn: FakeConn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unmapped failures keep the same shape as mapped ones, minus the detail."""

    async def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("password=hunter2 in a stack trace")

    monkeypatch.setattr(conn, "fetch", boom)
    resp = await client.get("/repos")
    assert resp.status_code == 500
    body = resp.json()
    assert body["detail"] == "internal server error"
    assert "hunter2" not in resp.text
    assert body["request_id"] == resp.headers["x-request-id"]


async def test_a_repo_error_from_the_worker_is_redacted(arq: FakeArq) -> None:
    """`repos.error` is served to the browser, so it is a user-facing field."""
    import uuid

    from app import worker
    from app.exceptions import CloneError

    recorded: list[tuple[str, tuple[Any, ...]]] = []

    class RecordingConn:
        async def execute(self, sql: str, *args: Any) -> str:
            recorded.append((sql, args))
            return "UPDATE 1"

        async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any]:
            # Non-None so the §15.4 lease claim succeeds; a worker that cannot
            # claim returns before ingesting, and this test needs it to ingest
            # and fail so the error text can be checked for redaction.
            recorded.append((sql, args))
            return {"id": args[0] if args else uuid.uuid4()}

    class Pool:
        def acquire(self) -> Any:
            conn = RecordingConn()

            class Ctx:
                async def __aenter__(self) -> Any:
                    return conn

                async def __aexit__(self, *_exc: Any) -> bool:
                    return False

            return Ctx()

    async def boom(*_a: Any, **_k: Any) -> Any:
        raise CloneError("clone failed for https://x:tok3n@github.com/a/b")

    import pytest as _pytest

    with _pytest.MonkeyPatch.context() as mp:
        mp.setattr(worker, "run_ingest", boom)
        await worker.ingest_repo({"pool": Pool()}, str(uuid.uuid4()))

    stored = recorded[-1][1][1]
    assert "tok3n" not in stored
    assert stored.startswith("CloneError:")
