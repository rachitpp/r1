"use client";

/**
 * useRepoChat — chat state over the §9 SSE stream (Phase 5 reconciliation:
 * this replaces the planned Vercel AI SDK `useChat`; DECISIONS 2026-07-27).
 *
 * One instance per repo chat page. `ask()` POSTs to `/repos/{id}/chat`,
 * dispatches §9 events into the current exchange, and appends the exchange to
 * the transcript on `done`/`error`. Completed exchanges persist to
 * sessionStorage (keyed by repo id) so a refresh restores the conversation; an
 * in-flight stream is deliberately not resumable.
 *
 * Delta rule (HANDOFF): `text` events are token-level on Mistral (~70 per
 * answer) but whole-message on a non-streaming provider — always accumulate.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { apiUrl } from "@/lib/api";
import type { Citation } from "@/lib/citations";
import {
  type ChatExchange,
  type ChatStatus,
  type ChatStep,
  type ToolLocation,
  emptyExchange,
} from "@/lib/chat-types";
import { SseRequestError, streamSse } from "@/lib/sse";

const storageKey = (repoId: string) => `chat:${repoId}`;

function loadTranscript(repoId: string): ChatExchange[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.sessionStorage.getItem(storageKey(repoId));
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? (parsed as ChatExchange[]) : [];
  } catch {
    return [];
  }
}

function saveTranscript(repoId: string, transcript: ChatExchange[]): void {
  try {
    window.sessionStorage.setItem(
      storageKey(repoId),
      JSON.stringify(transcript),
    );
  } catch {
    // Quota or private-mode failure: the app keeps working, refresh loses it.
  }
}

export interface RepoChat {
  transcript: ChatExchange[];
  /** The exchange currently streaming, or null when idle. */
  current: ChatExchange | null;
  status: ChatStatus;
  /** Set when the server answered 409: the repo regressed to this status. */
  notReadyStatus: string | null;
  ask: (question: string) => void;
  clear: () => void;
}

export function useRepoChat(repoId: string): RepoChat {
  const [transcript, setTranscript] = useState<ChatExchange[]>([]);
  const [current, setCurrent] = useState<ChatExchange | null>(null);
  const [status, setStatus] = useState<ChatStatus>("idle");
  const [notReadyStatus, setNotReadyStatus] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // Restore after mount: sessionStorage does not exist during SSR, and reading
  // it in the initial state would make hydration mismatch.
  useEffect(() => {
    setTranscript(loadTranscript(repoId));
    setStatus("idle");
    setCurrent(null);
  }, [repoId]);

  // Abort any live stream when the page unmounts.
  useEffect(() => () => abortRef.current?.abort(), []);

  const finish = useCallback(
    (exchange: ChatExchange, finalStatus: ChatStatus) => {
      setTranscript((prev) => {
        const next = [...prev, exchange];
        saveTranscript(repoId, next);
        return next;
      });
      setCurrent(null);
      setStatus(finalStatus);
    },
    [repoId],
  );

  const ask = useCallback(
    (question: string) => {
      if (abortRef.current) abortRef.current.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      setNotReadyStatus(null);

      // The exchange is mutated locally and mirrored into state per event —
      // React state is the render mirror, `exchange` is the source of truth
      // for the duration of the stream.
      const exchange = emptyExchange(question);
      setCurrent({ ...exchange });
      setStatus("thinking");

      void (async () => {
        try {
          for await (const { event, data } of streamSse(
            apiUrl(`/repos/${repoId}/chat`),
            { question },
            controller.signal,
          )) {
            const payload = JSON.parse(data);
            switch (event) {
              case "status":
                break; // "thinking" is already the initial status
              case "tool_call": {
                const step: ChatStep = {
                  n: payload.n,
                  tool: payload.tool,
                  args: payload.args ?? {},
                };
                exchange.steps = [...exchange.steps, step];
                break;
              }
              case "tool_result": {
                exchange.steps = exchange.steps.map((s) =>
                  s.n === payload.n
                    ? {
                        ...s,
                        summary: payload.summary as string,
                        locations: (payload.locations ?? []) as ToolLocation[],
                      }
                    : s,
                );
                break;
              }
              case "text":
                exchange.answer += payload.delta ?? "";
                setStatus("streaming");
                break;
              case "citations":
                exchange.citations = (payload.citations ?? []) as Citation[];
                break;
              case "done":
                exchange.toolCallsUsed = payload.tool_calls_used ?? null;
                finish({ ...exchange }, "done");
                return;
              case "error":
                exchange.error = payload.message ?? "stream error";
                finish({ ...exchange }, "error");
                return;
            }
            setCurrent({ ...exchange });
          }
          // Stream ended without done/error: keep what arrived, flag it.
          exchange.error ??= "stream ended unexpectedly";
          finish({ ...exchange }, "error");
        } catch (err) {
          if (controller.signal.aborted) return; // unmount/new question
          if (err instanceof SseRequestError && err.status === 409) {
            // Repo regressed to not-ready (e.g. re-ingest kicked off). The
            // page redirects; do not record an exchange.
            setNotReadyStatus(err.repoStatus ?? "unknown");
            setCurrent(null);
            setStatus("idle");
            return;
          }
          exchange.error =
            err instanceof Error ? err.message : "request failed";
          finish({ ...exchange }, "error");
        }
      })();
    },
    [repoId, finish],
  );

  const clear = useCallback(() => {
    abortRef.current?.abort();
    setTranscript([]);
    setCurrent(null);
    setStatus("idle");
    saveTranscript(repoId, []);
  }, [repoId]);

  return { transcript, current, status, notReadyStatus, ask, clear };
}
