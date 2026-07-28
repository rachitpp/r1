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
