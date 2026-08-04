/**
 * Transport for the backend HTTP API (SPEC §8) — base URL, error type, fetch.
 *
 * Split out of `api.ts` so the three concerns that file used to carry are
 * separable: this is the *how*, `api-types.ts` is the frozen contract, and
 * `api.ts` is the endpoint list. Nothing else in the app hand-writes a fetch to
 * the API. The chat stream is the one exception: it lives in `useRepoChat`
 * because its lifetime belongs to the hook, but it uses `apiUrl()` from here.
 */

export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export function apiUrl(path: string): string {
  return `${API_URL}${path}`;
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

/**
 * The one fetch wrapper. Exported for `api.ts` only — features call the named
 * endpoint functions there, never this.
 */
export async function request<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
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
