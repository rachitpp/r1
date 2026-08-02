/**
 * Typed client for the backend HTTP API (SPEC §8).
 *
 * Response shapes mirror the backend's Pydantic models exactly — this file is
 * the frontend's copy of the frozen contract, and nothing else in the app
 * hand-writes a fetch to the API. The chat stream is the one exception: it
 * lives in `useRepoChat` because its lifetime belongs to the hook, but it uses
 * `apiUrl()` from here for the base URL.
 */

export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export function apiUrl(path: string): string {
  return `${API_URL}${path}`;
}

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

/**
 * Error carrying the HTTP status and the backend's `detail`, when present.
 *
 * `requestId` is the backend's correlation id for the failed request (also in
 * the `X-Request-ID` response header). Quoting it in a bug report is what finds
 * the server-side log line, which matters because 5xx bodies deliberately say
 * only "internal server error" — the real message stays on the server.
 */
export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly detail: string,
    public readonly requestId: string | null = null,
  ) {
    super(detail);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let resp: Response;
  try {
    resp = await fetch(apiUrl(path), {
      ...init,
      // The session is an HttpOnly cookie the API sets (SPEC §13.4), so every
      // call has to opt in to sending it — a cross-origin fetch omits cookies
      // by default, and without this every request is anonymous and 401s.
      credentials: "include",
      headers: { "content-type": "application/json", ...init?.headers },
    });
  } catch {
    throw new ApiError(0, "API unreachable — is the backend running?");
  }
  if (!resp.ok) {
    let detail = `${resp.status} ${resp.statusText}`;
    let requestId = resp.headers.get("x-request-id");
    try {
      const body = await resp.json();
      if (typeof body?.detail === "string") detail = body.detail;
      // FastAPI's own 422 validation errors carry a list, not a string.
      else if (Array.isArray(body?.detail) && body.detail[0]?.msg)
        detail = body.detail[0].msg;
      if (typeof body?.request_id === "string") requestId = body.request_id;
    } catch {
      // Non-JSON error body — keep the status line.
    }
    throw new ApiError(resp.status, detail, requestId);
  }
  return resp.json() as Promise<T>;
}

export async function listRepos(): Promise<RepoOut[]> {
  const data = await request<{ repos: RepoOut[] }>("/repos");
  return data.repos;
}

export function getRepo(id: string): Promise<RepoOut> {
  return request<RepoOut>(`/repos/${id}`);
}

/**
 * Submit a URL. 201 = new repo queued; 200 = URL already known (which also
 * re-queues a `ready` or `failed` repo — the Retry button is this same call).
 */
export function createRepo(url: string): Promise<RepoOut> {
  return request<RepoOut>("/repos", {
    method: "POST",
    body: JSON.stringify({ url }),
  });
}

export function getFile(repoId: string, path: string): Promise<FileOut> {
  return request<FileOut>(
    `/repos/${repoId}/files?path=${encodeURIComponent(path)}`,
  );
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

export function getOverview(
  repoId: string,
  retry = false,
): Promise<OverviewOut> {
  return request<OverviewOut>(
    `/repos/${repoId}/overview${retry ? "?retry=true" : ""}`,
  );
}

export function getArchitecture(
  repoId: string,
  includeTests = false,
): Promise<ArchitectureOut> {
  return request<ArchitectureOut>(
    `/repos/${repoId}/architecture?include_tests=${includeTests}`,
  );
}

export function getCoverage(
  repoId: string,
  path: string,
): Promise<CoverageOut> {
  return request<CoverageOut>(
    `/repos/${repoId}/coverage?path=${encodeURIComponent(path)}`,
  );
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

export function getChecklist(repoId: string): Promise<ChecklistOut> {
  return request<ChecklistOut>(`/repos/${repoId}/checklist`);
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

export function shareAnswer(
  repoId: string,
  body: {
    question: string;
    answer: string;
    citations: { file_path: string; start_line: number; end_line: number }[];
    model?: string | null;
  },
): Promise<{ id: string }> {
  return request<{ id: string }>(`/repos/${repoId}/share`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function getSharedAnswer(shareId: string): Promise<SharedAnswerOut> {
  return request<SharedAnswerOut>(`/shared/${shareId}`);
}

export function getHistory(
  repoId: string,
  opts: { path?: string; limit?: number } = {},
): Promise<HistoryOut> {
  const params = new URLSearchParams();
  if (opts.path) params.set("path", opts.path);
  if (opts.limit) params.set("limit", String(opts.limit));
  const query = params.toString();
  return request<HistoryOut>(
    `/repos/${repoId}/history${query ? `?${query}` : ""}`,
  );
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

/**
 * Where to send the browser to sign in.
 *
 * A full-page navigation, never a fetch: the OAuth flow is three redirects
 * across two origins, and XHR cannot follow a cross-origin redirect chain or
 * let the user see GitHub's consent screen.
 */
export const loginUrl = apiUrl("/auth/github/login");

/**
 * The signed-in user, or `null` when nobody is.
 *
 * 401 is the *expected* answer for a signed-out visitor, so it is translated
 * to `null` rather than thrown — a logged-out homepage is not an error state.
 * Every other failure still throws.
 */
export async function getMe(): Promise<UserOut | null> {
  try {
    return await request<UserOut>("/auth/me");
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) return null;
    throw err;
  }
}

export async function logout(): Promise<void> {
  await fetch(apiUrl("/auth/logout"), {
    method: "POST",
    credentials: "include",
  });
}
