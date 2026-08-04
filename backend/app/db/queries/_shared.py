"""Constants and row types shared across the query modules.

Split out of the former single ``queries.py`` so each module can import what it
needs without importing its siblings. Nothing here runs a statement.
"""

from __future__ import annotations

# Column order for chunk inserts — the embedding is last. ``id`` (identity),
# ``tsv`` (generated), ``part``/``n_parts`` defaults are handled by the table.
ChunkRow = tuple[
    str,  # file_path
    bool,  # is_test (derived from file_path, SPEC §2.6)
    str | None,  # symbol
    str,  # kind
    int,  # part
    int,  # n_parts
    int,  # start_line
    int,  # end_line
    str,  # header
    str,  # code
    list[float],  # embedding
]
FileRow = tuple[str, str, int]  # path, content, n_lines


# SPEC §10 state machine:
#   queued -> cloning -> parsing -> linking -> embedding -> ready | failed
#
# IN_FLIGHT_STATUSES is what the zombie sweep considers abandoned work. It
# excludes ``queued`` deliberately: a queued repo's job lives in Redis, which
# redelivers it when a worker returns, so a long queue wait is not a zombie.
# Only states a worker enters *while holding* the job can be orphaned by its
# death.
REPO_STATUSES: tuple[str, ...] = (
    "queued",
    "cloning",
    "parsing",
    "linking",
    "embedding",
    "ready",
    "failed",
)
IN_FLIGHT_STATUSES: tuple[str, ...] = ("cloning", "parsing", "linking", "embedding")


STRATEGIES: tuple[str, ...] = ("ast", "naive")

SNAPSHOT_COLUMNS = (
    "sn.id, s.url, s.name, sn.status, sn.error, sn.commit_sha AS head_sha, "
    "sn.default_branch, sn.files_total, sn.files_parsed, sn.chunks_total, "
    "sn.chunks_embedded, sn.created_at"
)
SNAPSHOT_FROM = "repo_snapshots sn JOIN repo_sources s ON s.id = sn.source_id"


# Everything a worker will get to without anyone submitting anything further.
# ``queued`` is included here and excluded from the zombie sweep, for the same
# reason in both cases: a queued job is real work that exists, it just has not
# started.
ACTIVE_STATUSES: tuple[str, ...] = ("queued", *IN_FLIGHT_STATUSES)
