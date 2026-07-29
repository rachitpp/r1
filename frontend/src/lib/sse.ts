/**
 * Minimal SSE parser over fetch + ReadableStream (Phase 5 reconciliation).
 *
 * The chat endpoint is a POST (§8), which rules out EventSource, and the §9
 * schema maps 1:1 onto UI state, which rules out adapting a third-party stream
 * protocol. What's left is ~60 lines of the SSE wire format:
 *
 *   - events are separated by a blank line
 *   - `event: <name>` names the next event (default "message")
 *   - `data: <text>` lines accumulate; multi-line data joins with "\n"
 *   - `:` lines are comments; unknown fields are ignored
 *   - a trailing partial event at stream end is dropped (never dispatched)
 *
 * The parser is a pure incremental function over text chunks so it can be
 * unit-tested without a network; `streamSse` binds it to a fetch body.
 */

export interface SseEvent {
  event: string;
  data: string;
}

/** Incremental parser: feed() text chunks, collect complete events. */
export function createSseParser(): {
  feed: (chunk: string) => SseEvent[];
} {
  let buffer = "";
  let eventName = "";
  let dataLines: string[] = [];

  function dispatchIfComplete(out: SseEvent[]) {
    if (dataLines.length > 0) {
      out.push({ event: eventName || "message", data: dataLines.join("\n") });
    }
    eventName = "";
    dataLines = [];
  }

  return {
    feed(chunk: string): SseEvent[] {
      buffer += chunk;
      const out: SseEvent[] = [];
      // Process only complete lines; keep the partial tail in the buffer.
      let idx: number;
      while ((idx = buffer.search(/\r\n|\r|\n/)) !== -1) {
        const line = buffer.slice(0, idx);
        buffer = buffer.slice(idx + (buffer[idx] === "\r" && buffer[idx + 1] === "\n" ? 2 : 1));
        if (line === "") {
          dispatchIfComplete(out);
        } else if (line.startsWith(":")) {
          // comment / keep-alive
        } else if (line.startsWith("event:")) {
          eventName = line.slice(6).trimStart();
        } else if (line.startsWith("data:")) {
          dataLines.push(line.slice(5).trimStart());
        }
        // Unknown fields (id:, retry:) are irrelevant to §9 — ignored.
      }
      return out;
    },
  };
}

/**
 * POST `body` to `url` and yield parsed SSE events until the stream ends.
 *
 * Non-2xx responses are surfaced as a thrown error carrying the status and the
 * JSON `detail` when present (the 409 not-ready body includes `status` too).
 * The caller owns the AbortSignal.
 */
export class SseRequestError extends Error {
  constructor(
    public readonly status: number,
    public readonly detail: string,
    public readonly repoStatus?: string,
  ) {
    super(detail);
    this.name = "SseRequestError";
  }
}

export async function* streamSse(
  url: string,
  body: unknown,
  signal?: AbortSignal,
): AsyncGenerator<SseEvent> {
  const resp = await fetch(url, {
    method: "POST",
    // Same reason as `lib/api.ts`: the session cookie is only sent when a
    // cross-origin request asks for it. This is also the payoff for parsing
    // SSE over `fetch` instead of using `EventSource`, which cannot send
    // credentials to another origin or set a header (DECISIONS 2026-07-27).
    credentials: "include",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!resp.ok) {
    let detail = `${resp.status} ${resp.statusText}`;
    let repoStatus: string | undefined;
    try {
      const parsed = await resp.json();
      if (typeof parsed?.detail === "string") detail = parsed.detail;
      if (typeof parsed?.status === "string") repoStatus = parsed.status;
    } catch {
      // non-JSON body; keep the status line
    }
    throw new SseRequestError(resp.status, detail, repoStatus);
  }
  if (!resp.body) throw new SseRequestError(0, "response has no body");

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  const parser = createSseParser();
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      for (const event of parser.feed(decoder.decode(value, { stream: true }))) {
        yield event;
      }
    }
  } finally {
    reader.releaseLock();
  }
}
