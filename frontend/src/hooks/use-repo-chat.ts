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

import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";

import { USER_QUERY_KEY } from "@/hooks/use-user";
import { apiUrl } from "@/lib/api";
import type { Citation } from "@/lib/citations";
import {
  CHAT_STORAGE_PREFIX,
  type ChatExchange,
  type ChatStatus,
  type ChatStep,
  type ToolLocation,
  emptyExchange,
} from "@/lib/chat-types";
import { SseRequestError, streamSse } from "@/lib/sse";

const storageKey = (repoId: string) => `${CHAT_STORAGE_PREFIX}${repoId}`;

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
  /** End the live stream and keep whatever arrived as a finished exchange. */
  stop: () => void;
  clear: () => void;
}

export function useRepoChat(repoId: string): RepoChat {
  const queryClient = useQueryClient();
  const [transcript, setTranscript] = useState<ChatExchange[]>([]);
  const [current, setCurrent] = useState<ChatExchange | null>(null);
  const [status, setStatus] = useState<ChatStatus>("idle");
  const [notReadyStatus, setNotReadyStatus] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  // Distinguishes the three reasons a stream can abort: the viewer pressed Stop
  // (keep the partial exchange), a new question replaced it, or the page
  // unmounted (both discard it).
  const stoppedRef = useRef(false);

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

  // §23: the conversation this transcript belongs to. Null until the first
  // answer establishes one; sent on every later turn so the agent gets the
  // prior exchange as context and the server appends rather than forking.
  const conversationRef = useRef<string | null>(null);

  const ask = useCallback(
    (question: string) => {
      if (abortRef.current) abortRef.current.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      stoppedRef.current = false;
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
            conversationRef.current
              ? { question, conversation_id: conversationRef.current }
              : { question },
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
                  startedAt: Date.now(),
                };
                exchange.steps = [...exchange.steps, step];
                setStatus("thinking"); // a new tool call: back to working
                break;
              }
              case "tool_result": {
                exchange.steps = exchange.steps.map((s) =>
                  s.n === payload.n
                    ? {
                        ...s,
                        summary: payload.summary as string,
                        locations: (payload.locations ?? []) as ToolLocation[],
                        ms: s.startedAt ? Date.now() - s.startedAt : undefined,
                      }
                    : s,
                );
                // Tools have produced something and no answer text has arrived
                // yet: the model is composing. Covers the quiet gap.
                if (!exchange.answer) setStatus("composing");
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
                // Captured on the first turn and sent on every later one, so
                // the agent reads the prior exchange as context (§23.2).
                if (payload.conversation_id)
                  conversationRef.current = payload.conversation_id;
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
          if (controller.signal.aborted) {
            // Stop is the one abort whose partial result is worth keeping.
            if (stoppedRef.current) {
              exchange.stopped = true;
              finish({ ...exchange }, "done");
            }
            return; // otherwise: unmount or a replacing question
          }
          if (err instanceof SseRequestError && err.status === 409) {
            // Repo regressed to not-ready (e.g. re-ingest kicked off). The
            // page redirects; do not record an exchange.
            setNotReadyStatus(err.repoStatus ?? "unknown");
            setCurrent(null);
            setStatus("idle");
            return;
          }
          if (err instanceof SseRequestError && err.status === 401) {
            // Session ended mid-stream (expired, or signed out in another tab).
            // Reset the user so RequireAuth re-gates this page to a sign-in
            // prompt, rather than leaving a raw "401" in a red error bubble.
            queryClient.setQueryData(USER_QUERY_KEY, null);
            setCurrent(null);
            setStatus("idle");
            return;
          }
          if (err instanceof SseRequestError && err.status === 429) {
            // A capacity/budget refusal (§17): nothing is broken, the box is
            // just busy — a calm message, not a "went wrong" alarm.
            exchange.error =
              err.detail ||
              "The service is busy right now — try again in a moment.";
            finish({ ...exchange }, "error");
            return;
          }
          exchange.error =
            err instanceof Error ? err.message : "request failed";
          finish({ ...exchange }, "error");
        }
      })();
    },
    [repoId, finish, queryClient],
  );

  const stop = useCallback(() => {
    if (!abortRef.current) return;
    stoppedRef.current = true;
    abortRef.current.abort();
  }, []);

  // "New chat" must start a new conversation, not append to the old one — the
  // transcript is cleared on screen, and the server-side thread it belonged to
  // has to be let go with it.
  const clear = useCallback(() => {
    conversationRef.current = null;
    stoppedRef.current = false;
    abortRef.current?.abort();
    setTranscript([]);
    setCurrent(null);
    setStatus("idle");
    saveTranscript(repoId, []);
  }, [repoId]);

  return { transcript, current, status, notReadyStatus, ask, stop, clear };
}
