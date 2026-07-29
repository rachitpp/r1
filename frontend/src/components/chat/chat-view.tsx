"use client";

/**
 * `/repos/[id]/chat` — the split pane. Conversation left, code viewer right.
 *
 * On narrow viewports the viewer is not a stacked strip but a sheet: it slides
 * over the conversation when a citation is chosen and dismisses, so the
 * conversation keeps the full screen the rest of the time.
 *
 * The viewer only ever shows what the reader asked for. It starts empty and
 * changes on exactly one event: a click on a citation chip or a step location.
 * It used to follow the agent — opening whatever file the last tool touched,
 * then the answer's first citation — which meant code the reader never asked
 * for appeared and then swapped itself out mid-read.
 */

import { useQuery } from "@tanstack/react-query";
import { ArrowRight, MessageSquarePlus, Square } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { ExchangeView } from "@/components/chat/exchange-view";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { CodeViewer } from "@/components/code-viewer";
import { Skeleton } from "@/components/ui/skeleton";
import { useRepoChat } from "@/hooks/use-repo-chat";
import { ApiError, getRepo } from "@/lib/api";
import { type Citation, citationKey } from "@/lib/citations";
import { cn } from "@/lib/utils";

/** Questions that make sense for any repo — one click to a live demo. */
const SUGGESTIONS = [
  "What does this project do?",
  "Where does execution start?",
  "How is the code organised?",
];

/** Auto-follow stops fighting the reader once they scroll away from the tail. */
const STICK_THRESHOLD_PX = 120;
const COMPOSER_MAX_PX = 160;

export function ChatView({ repoId }: { repoId: string }) {
  const router = useRouter();
  const repo = useQuery({
    queryKey: ["repo", repoId],
    queryFn: () => getRepo(repoId),
    retry: (count, err) =>
      !(err instanceof ApiError && err.status === 404) && count < 1,
  });
  const chat = useRepoChat(repoId);
  const [question, setQuestion] = useState("");
  const [selection, setSelection] = useState<Citation | null>(null);

  const bottomRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  /** True while the transcript is scrolled near its tail. */
  const stickRef = useRef(true);

  const streaming =
    chat.status === "thinking" ||
    chat.status === "composing" ||
    chat.status === "streaming";

  // Follow the stream: new steps/deltas keep the latest content in view. The
  // hook replaces `current` with a fresh object per event, so its answer
  // length + step count are stable primitives to depend on.
  const liveSize = chat.current
    ? chat.current.answer.length + chat.current.steps.length
    : 0;
  useEffect(() => {
    if (streaming && stickRef.current) {
      bottomRef.current?.scrollIntoView({ block: "end" });
    }
  }, [streaming, liveSize]);

  // Keep the composer sized to its content, up to a cap.
  useEffect(() => {
    const el = composerRef.current;
    if (!el) return;
    el.style.height = "0px";
    el.style.height = `${Math.min(el.scrollHeight, COMPOSER_MAX_PX)}px`;
  }, [question]);

  // 409 mid-session: the repo regressed to not-ready (e.g. a re-ingest).
  // The status page is the right place to wait it out.
  useEffect(() => {
    if (chat.notReadyStatus) router.push(`/repos/${repoId}`);
  }, [chat.notReadyStatus, router, repoId]);

  const send = (text: string) => {
    const q = text.trim();
    if (!q || streaming) return;
    stickRef.current = true;
    chat.ask(q);
    setQuestion("");
  };

  /** The only thing that ever fills the viewer. */
  const pick = (c: Citation) => setSelection(c);

  const newConversation = () => {
    chat.clear();
    setSelection(null);
    setQuestion("");
  };

  if (repo.isPending) {
    return (
      <div className="page-container space-y-4 py-10">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }
  if (repo.isError) {
    const notFound = repo.error instanceof ApiError && repo.error.status === 404;
    return (
      <div className="page-container py-10">
        <Alert variant="destructive">
          <AlertTitle>{notFound ? "Repository not found" : "Error"}</AlertTitle>
          <AlertDescription className="space-y-3">
            <p>
              {notFound
                ? "No repository with this id exists."
                : repo.error instanceof ApiError
                  ? repo.error.detail
                  : "Unexpected error."}
            </p>
            <Button asChild variant="outline" size="sm">
              <Link href="/">Back to repositories</Link>
            </Button>
          </AlertDescription>
        </Alert>
      </div>
    );
  }
  if (repo.data.status !== "ready") {
    // Direct navigation to the chat of an unready repo: send them to status.
    return (
      <div className="page-container py-10">
        <Alert>
          <AlertTitle>This repository is not ready yet</AlertTitle>
          <AlertDescription className="space-y-3">
            <p>
              Current status:{" "}
              <span className="font-mono">{repo.data.status}</span>
            </p>
            <Button asChild size="sm">
              <Link href={`/repos/${repoId}`}>View indexing progress</Link>
            </Button>
          </AlertDescription>
        </Alert>
      </div>
    );
  }

  const activeKey = selection ? citationKey(selection) : null;
  const empty = chat.transcript.length === 0 && !chat.current;
  const progress = repo.data.progress;

  return (
    // Full-bleed on purpose: this is an app shell, not a document column. A
    // centred max-width leaves dead background either side of the panes, which
    // is the one thing a split view must not do.
    <div className="flex h-[calc(100vh-3rem-1px)] w-full flex-col lg:flex-row">
      {/* Left — conversation. `min-w-0` for the same reason as the viewer
          below: a flex item defaults to `min-width: auto` and will not shrink
          below its content. */}
      <section className="flex min-h-0 min-w-0 flex-1 flex-col lg:border-r">
        {/* Three levels, not three same-weight links: the repo names the pane,
            its counts are meta beneath, and only the one action that changes
            state ("New chat") carries a border. */}
        <div className="flex items-center justify-between gap-3 border-b px-4 py-2">
          <div className="flex min-w-0 flex-col">
            <span className="truncate font-mono text-sm font-medium leading-tight">
              {repo.data.name}
            </span>
            <span className="hidden truncate text-[11px] leading-tight text-muted-foreground sm:inline">
              {progress.files_total} files · {progress.chunks_total} chunks
            </span>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <Link
              href={`/repos/${repoId}`}
              className="rounded-sm text-xs text-muted-foreground underline decoration-border decoration-dotted underline-offset-4 transition-colors hover:text-foreground"
            >
              status
            </Link>
            {chat.transcript.length > 0 && (
              <button
                type="button"
                onClick={newConversation}
                className="inline-flex items-center gap-1.5 rounded-sm border px-2 py-1 text-xs text-muted-foreground transition-colors hover:border-primary/40 hover:text-foreground"
              >
                <MessageSquarePlus className="size-3.5" />
                New chat
              </button>
            )}
          </div>
        </div>

        <div
          ref={scrollRef}
          onScroll={(e) => {
            const el = e.currentTarget;
            stickRef.current =
              el.scrollHeight - el.scrollTop - el.clientHeight <
              STICK_THRESHOLD_PX;
          }}
          className="min-h-0 flex-1 space-y-8 overflow-y-auto px-4 py-6"
        >
          {/* Left-aligned and set in display type, with the suggestions as a
              ruled list of real questions. The centred grey paragraph and row
              of pills this replaced is the stock generated empty state, and it
              was occupying the largest blank surface in the app. */}
          {empty && (
            <div className="max-w-xl pt-6">
              <p className="eyebrow">Ready</p>
              <h2 className="display mt-4 text-2xl font-semibold leading-tight">
                Ask this codebase a question.
              </h2>
              <p className="mt-3 text-sm leading-relaxed text-muted-foreground">
                Every tool call the agent makes streams here as it happens —
                search, then graph traversal into the definitions the search
                missed. The code it cites opens on the right, at the line.
              </p>

              <ul className="mt-6 divide-y border-y">
                {SUGGESTIONS.map((s) => (
                  <li key={s}>
                    <button
                      type="button"
                      onClick={() => send(s)}
                      className="group flex w-full items-center gap-3 py-2.5 text-left transition-colors hover:text-primary"
                    >
                      <span
                        aria-hidden
                        className="h-px w-4 shrink-0 bg-border transition-all group-hover:w-6 group-hover:bg-primary"
                      />
                      <span className="display text-[15px]">{s}</span>
                      <ArrowRight className="ml-auto size-3.5 shrink-0 -translate-x-1 text-primary opacity-0 transition-all group-hover:translate-x-0 group-hover:opacity-100" />
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {chat.transcript.map((exchange, i) => (
            <ExchangeView
              key={i}
              exchange={exchange}
              onCiteClick={pick}
              activeKey={activeKey}
              onRegenerate={streaming ? undefined : send}
            />
          ))}
          {chat.current && (
            <ExchangeView
              exchange={chat.current}
              live
              status={chat.status}
              onCiteClick={pick}
              activeKey={activeKey}
            />
          )}
          <div ref={bottomRef} />
        </div>

        <form
          className="border-t p-3"
          onSubmit={(e) => {
            e.preventDefault();
            send(question);
          }}
        >
          <div className="flex items-end gap-2">
            <textarea
              ref={composerRef}
              rows={1}
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => {
                // Enter sends; Shift+Enter is a newline. An IME composition
                // ends on Enter too, and must not send.
                if (
                  e.key === "Enter" &&
                  !e.shiftKey &&
                  !e.nativeEvent.isComposing
                ) {
                  e.preventDefault();
                  send(question);
                }
              }}
              placeholder={
                streaming ? "Waiting for the answer…" : "Ask a question…"
              }
              aria-label="Question"
              className="flex max-h-40 min-h-[42px] w-full resize-none rounded-md border border-input bg-card px-3 py-2.5 text-sm shadow-none placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:cursor-not-allowed disabled:opacity-50"
            />
            {streaming ? (
              <Button
                type="button"
                variant="outline"
                onClick={chat.stop}
                className="h-[42px] shrink-0 rounded-md"
              >
                <Square className="size-3.5 fill-current" />
                Stop
              </Button>
            ) : (
              <Button type="submit" className="h-[42px] shrink-0 rounded-md px-5">
                Ask
              </Button>
            )}
          </div>
          {/* Keyboard chrome only. The 8-call cap used to be tacked on here,
              where it read as a third shortcut; it now lives in the timeline
              header, counting up against the budget as it is spent. */}
          <p className="mt-1.5 px-1 text-[11px] text-muted-foreground">
            <kbd className="font-mono">Enter</kbd> to send ·{" "}
            <kbd className="font-mono">Shift+Enter</kbd> for a new line
          </p>
        </form>
      </section>

      {/* Right — code viewer. A sheet below lg, a pane at lg and up.
          `min-w-0` is load-bearing: the viewer renders its `pre` at `w-max` so
          unwrapped lines can scroll, and without it this flex item grows to the
          widest line in the file and scrolls the whole document sideways —
          header and conversation included. */}
      <section
        className={cn(
          "fixed inset-x-0 bottom-0 top-12 z-50 border-t bg-card shadow-2xl transition-transform duration-200",
          selection ? "translate-y-0" : "translate-y-full",
          "lg:static lg:z-auto lg:w-[44%] lg:min-w-0 lg:translate-y-0 lg:border-t-0 lg:shadow-none",
        )}
      >
        <CodeViewer
          repoId={repoId}
          selection={selection}
          repoUrl={repo.data.url}
          headSha={repo.data.head_sha}
          onClose={() => setSelection(null)}
        />
      </section>
    </div>
  );
}
