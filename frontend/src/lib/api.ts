/**
 * The endpoint list for the backend HTTP API (SPEC §8).
 *
 * One function per route, nothing else — transport lives in `api-client.ts`
 * and the response shapes in `api-types.ts`. Features import from here to
 * *call* the API and from `api-types` to *describe* what comes back.
 */

import { apiUrl, ApiError, request } from "@/lib/api-client";
import type {
  ArchitectureOut,
  ChecklistOut,
  CompareOut,
  CoverageOut,
  DependenciesOut,
  DependencyUsesOut,
  FileOut,
  HistoryOut,
  OverviewOut,
  RepoOut,
  SharedAnswerOut,
  SiblingSnapshot,
  TraceOut,
  UserOut,
} from "@/lib/api-types";

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
export function createRepo(url: string, rev?: string): Promise<RepoOut> {
  return request<RepoOut>("/repos", {
    method: "POST",
    // `rev` pins a commit instead of the branch tip (SPEC §28.3). Omitted
    // entirely when absent rather than sent as null, so the common path posts
    // exactly the body it always did.
    body: JSON.stringify(rev ? { url, rev } : { url }),
  });
}

/** `GET /repos/{id}/snapshots` — what this snapshot can be compared against. */
export function getSiblingSnapshots(
  repoId: string,
): Promise<{ siblings: SiblingSnapshot[] }> {
  return request<{ siblings: SiblingSnapshot[] }>(`/repos/${repoId}/snapshots`);
}

/** `GET /repos/{id}/compare?base=` — this snapshot, against an earlier one. */
export function getComparison(
  repoId: string,
  baseId: string,
): Promise<CompareOut> {
  return request<CompareOut>(
    `/repos/${repoId}/compare?base=${encodeURIComponent(baseId)}`,
  );
}

export function getFile(repoId: string, path: string): Promise<FileOut> {
  return request<FileOut>(
    `/repos/${repoId}/files?path=${encodeURIComponent(path)}`,
  );
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

/** `GET /repos/{id}/dependencies` (SPEC §26.2). */
export function getDependencies(
  repoId: string,
  includeTests = false,
): Promise<DependenciesOut> {
  return request<DependenciesOut>(
    `/repos/${repoId}/dependencies?include_tests=${includeTests}`,
  );
}

/** `GET /repos/{id}/dependencies/{module}` — every import site for one package. */
export function getDependencyUses(
  repoId: string,
  module: string,
  includeTests = false,
): Promise<DependencyUsesOut> {
  return request<DependencyUsesOut>(
    `/repos/${repoId}/dependencies/${encodeURIComponent(module)}` +
      `?include_tests=${includeTests}`,
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

export function getChecklist(repoId: string): Promise<ChecklistOut> {
  return request<ChecklistOut>(`/repos/${repoId}/checklist`);
}

export function getTrace(
  repoId: string,
  symbol: string,
  opts: { direction?: "in" | "out"; depth?: number } = {},
): Promise<TraceOut> {
  const params = new URLSearchParams({ symbol });
  if (opts.direction) params.set("direction", opts.direction);
  if (opts.depth) params.set("depth", String(opts.depth));
  return request<TraceOut>(`/repos/${repoId}/trace?${params}`);
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
