/**
 * Chat state shapes shared by the hook and the chat UI (SPEC §9).
 *
 * A `ChatExchange` is one question and everything the stream produced for it.
 * Completed exchanges are persisted to sessionStorage; the in-flight one lives
 * only in memory (a refresh cuts it — accepted, per the phase prompt).
 */

import type { Citation } from "@/lib/citations";

export interface ToolLocation {
  file_path: string;
  start_line: number;
  end_line: number;
}

/** One tool_call, later completed in place by its tool_result (same `n`). */
export interface ChatStep {
  n: number;
  tool: string;
  args: Record<string, unknown>;
  summary?: string;
  locations?: ToolLocation[];
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
