/**
 * The frontend's copy of the backend's frozen response contract (SPEC §8).
 *
 * Every shape here mirrors a Pydantic model exactly. Kept apart from the fetch
 * functions in `api.ts` because it is read far more often than it is called:
 * most consumers want a type, not an endpoint. Naming follows `chat-types.ts`.
 */

export type RepoStatus =
  | "queued"
  | "cloning"
  | "parsing"
  | "linking"
  | "embedding"
  | "ready"
  | "failed";

/** §10: states where a worker currently owns the job. */
export const IN_FLIGHT_STATUSES: readonly RepoStatus[] = [
  "queued",
  "cloning",
  "parsing",
  "linking",
  "embedding",
];

export interface RepoProgress {
  files_total: number;
  files_parsed: number;
  chunks_total: number;
  chunks_embedded: number;
}

export interface RepoOut {
  id: string;
  url: string;
  name: string;
  status: RepoStatus;
  error: string | null;
  head_sha: string | null;
  progress: RepoProgress;
  created_at: string;
}

export interface FileOut {
  path: string;
  content: string;
  /** Lines in the whole file, even when a range was requested. */
  n_lines: number;
  /** The range `content` actually covers; 1..n_lines for a whole-file read. */
  start_line: number;
  end_line: number;
}

/* ---------------------------------------------------------------------------
 * §28 snapshot comparison. A structural diff between two corpora of one repo.
 * ------------------------------------------------------------------------- */

export interface SnapshotRef {
  id: string;
  commit_sha: string | null;
  strategy: string;
  created_at: string;
}

export interface ChangedSymbol {
  qualname: string;
  kind: string;
  file_path: string;
}

export interface CompareCommit {
  sha: string;
  author_name: string;
  authored_at: string;
  subject: string;
}

export interface CompareOut {
  base: SnapshotRef;
  head: SnapshotRef;
  files_added: string[];
  files_removed: string[];
  symbols_added: ChangedSymbol[];
  symbols_removed: ChangedSymbol[];
  dependencies_added: string[];
  dependencies_removed: string[];
  /** False when either side predates the §20 history pass. */
  commits_indexed: boolean;
  commits: CompareCommit[];
  truncated: boolean;
}

export interface SiblingSnapshot {
  id: string;
  commit_sha: string | null;
  status: string;
  created_at: string;
}

/* -------------------------------------------------------------------------
 * Graph views (SPEC §18)
 *
 * Both are deterministic reads over the symbol graph — no model, no tool
 * budget, no tokens. Snapshots are immutable (§14.3), so the answers cannot
 * change for a given repo id and TanStack may hold them indefinitely.
 * ---------------------------------------------------------------------- */

export interface ModuleNode {
  path: string;
  n_symbols: number;
  /** Edges arriving from other modules — "how much depends on this". */
  fan_in: number;
  fan_out: number;
}

export interface ModuleEdge {
  from_path: string;
  to_path: string;
  kind: string;
  /** Symbol-level edges of this kind crossing the pair. */
  weight: number;
}

export interface ArchitectureOut {
  nodes: ModuleNode[];
  edges: ModuleEdge[];
  include_tests: boolean;
  /** Either list hit its §12 cap — the map is the top of the ranking, not all of it. */
  truncated: boolean;
}

/** A pointer at one symbol. Never carries a code body — that is `/files`. */
export interface SymbolRef {
  qualname: string;
  file_path: string;
  line: number;
}

export interface CoveredSymbol {
  name: string;
  qualname: string;
  kind: string;
  start_line: number;
  end_line: number;
  tests: SymbolRef[];
}

export interface CoverageOut {
  path: string;
  /** Symbols defined here, each with the tests that reach it. */
  covered: CoveredSymbol[];
  /** What this file reaches, when it is itself a test file. Empty otherwise. */
  covers: SymbolRef[];
  truncated: boolean;
}

/**
 * `GET /repos/{id}/overview` (SPEC §19.4).
 *
 * `generating` arrives with HTTP 202 and no body — the server has claimed the
 * row and queued one model call. Poll; do not re-request in a way that could
 * enqueue again (it cannot, the primary key prevents it, but the intent
 * matters). `failed` carries an error and is retryable exactly once per
 * explicit request.
 */
export interface OverviewOut {
  status: "generating" | "ready" | "failed";
  body: string | null;
  citations: { file_path: string; start_line: number; end_line: number }[];
  model: string | null;
  error: string | null;
}

/* ---------------------------------------------------------------------------
 * §26 dependencies. What the repo stands on, and what it declares.
 * ------------------------------------------------------------------------- */

export interface DependencyOut {
  module: string;
  n_uses: number;
  n_files: number;
  /** Matched against the manifests by normalised name — see `declared` in SPEC §26.2. */
  declared: boolean;
  requirement: string | null;
  sources: string[];
  extras: string[];
}

export interface UnusedDependency {
  name: string;
  requirement: string;
  sources: string[];
  extras: string[];
}

export interface DependenciesOut {
  /** False for a snapshot ingested before the pass existed (§26.3). */
  indexed: boolean;
  include_tests: boolean;
  packages: DependencyOut[];
  undeclared: string[];
  unused: UnusedDependency[];
  truncated: boolean;
}

export interface DependencyUse {
  dotted: string;
  file_path: string;
  start_line: number;
  is_test: boolean;
}

export interface DependencyUsesOut {
  module: string;
  include_tests: boolean;
  uses: DependencyUse[];
  truncated: boolean;
}

/**
 * `GET /repos/{id}/checklist` (SPEC §22.2).
 *
 * Derived from the symbol graph — no model call — so it is deterministic and,
 * like the §18 views, cacheable for as long as the client holds the repo id.
 * Fewer than five items is normal: steps that do not apply are absent rather
 * than padded.
 */
export interface ChecklistItem {
  kind: string;
  title: string;
  detail: string;
  file_path: string;
  start_line: number;
  end_line: number;
  /** Pre-filled into `/chat?q=` — the launch-point 3.5 built. */
  question: string;
}

export interface ChecklistOut {
  items: ChecklistItem[];
}

/**
 * `GET /repos/{id}/trace` (SPEC §24.2) — a bounded transitive walk.
 *
 * Pointers, never code: `expand_context` returns bodies for a model to read,
 * this returns a path for a person to follow. `via` plus `depth` reconstructs
 * the chain without the server sending one per node.
 */
export interface TraceNode {
  depth: number;
  kind: string | null;
  name: string;
  qualname: string;
  file_path: string;
  start_line: number;
  end_line: number;
  via: string | null;
}

export interface TraceOut {
  root: SymbolRef;
  direction: "in" | "out";
  max_depth: number;
  nodes: TraceNode[];
  truncated: boolean;
}

export interface CommitOut {
  sha: string;
  author_name: string;
  author_email: string | null;
  authored_at: string;
  subject: string;
  body: string | null;
  is_merge: boolean;
  /** Scoped to the requested path when one was given; commit-wide otherwise. */
  insertions: number;
  deletions: number;
}

/**
 * `GET /repos/{id}/history` (SPEC §20.2).
 *
 * `indexed` is the field to read before `commits`. Every snapshot ingested
 * before §20 returns an empty list with `indexed: false`, which means "nobody
 * walked the log", not "this file has no history" — rendering those the same
 * way is the bug the flag exists to prevent.
 */
export interface HistoryOut {
  path: string | null;
  indexed: boolean;
  include_merges: boolean;
  commits: CommitOut[];
  truncated: boolean;
}

/**
 * `GET /shared/{id}` (SPEC §21.3) — the one read in this API with no session.
 *
 * Carries the repo's URL and pinned commit alongside the answer, so a reader
 * with no account can still resolve every citation to a GitHub blob link at the
 * exact commit the answer was written against.
 */
export interface SharedAnswerOut {
  id: string;
  question: string;
  answer: string;
  citations: { file_path: string; start_line: number; end_line: number }[];
  model: string | null;
  created_at: string;
  repo_name: string;
  repo_url: string;
  commit_sha: string | null;
}

/* -------------------------------------------------------------------------
 * Identity (SPEC §13)
 * ---------------------------------------------------------------------- */

/** `GET /auth/me`. Mirrors the backend's `UserOut` — no `github_id` (§13.2). */
export interface UserOut {
  id: string;
  login: string;
  name: string | null;
  avatar_url: string | null;
  created_at: string;
}
