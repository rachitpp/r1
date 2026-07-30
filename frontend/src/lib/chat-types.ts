/**
 * Chat state shapes shared by the hook and the chat UI (SPEC §9).
 *
 * A `ChatExchange` is one question and everything the stream produced for it.
 * Completed exchanges are persisted to sessionStorage; the in-flight one lives
 * only in memory (a refresh cuts it — accepted, per the phase prompt).
 */

import type { Citation } from "@/lib/citations";

/**
 * sessionStorage namespace for saved transcripts: `chat:<repoId>`. Defined here,
 * the leaf both the chat hook and sign-out reach, so the two never drift — the
 * hook writes under it, and logout clears everything under it (§13 teardown).
 */
export const CHAT_STORAGE_PREFIX = "chat:";

export interface ToolLocation {
  file_path: string;
  start_line: number;
  end_line: number;
}

/**
 * One tool_call, later completed in place by its tool_result (same `n`).
 *
 * §9 carries no timing, so `startedAt`/`ms` are measured on the client between
 * the two events. That is wall-clock including transport, which is the number a
 * viewer actually experiences.
 */
export interface ChatStep {
  n: number;
  tool: string;
  args: Record<string, unknown>;
  summary?: string;
  locations?: ToolLocation[];
  startedAt?: number;
  ms?: number;
}

/**
 * `composing` is derived on the client, not sent by the server: §9 has no event
 * for "tools are done, the model is writing". The ~10s silence between the last
 * `tool_result` and the first `text` delta reads as a stall without it
 * (HANDOFF, Phase 6 hardening input).
 */
export type ChatStatus =
  | "idle"
  | "thinking"
  | "composing"
  | "streaming"
  | "done"
  | "error";

export interface ChatExchange {
  question: string;
  steps: ChatStep[];
  answer: string;
  citations: Citation[];
  toolCallsUsed: number | null;
  error: string | null;
  /** True when the viewer pressed Stop: whatever streamed is kept, and the
   * exchange is not an error. */
  stopped?: boolean;
}

export function emptyExchange(question: string): ChatExchange {
  return {
    question,
    steps: [],
    answer: "",
    citations: [],
    toolCallsUsed: null,
    error: null,
  };
}
