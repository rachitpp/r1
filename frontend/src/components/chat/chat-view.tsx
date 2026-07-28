"use client";

/**
 * `/repos/[id]/chat` — the split pane. Conversation left, code viewer right.
 *
 * On narrow viewports the viewer is not a stacked strip but a sheet: it slides
 * over the conversation when a citation is chosen and dismisses, so the
 * conversation keeps the full screen the rest of the time.
 *
 * The pane also follows the agent. Unless the viewer has picked a citation
 * themselves, each tool result opens the file the agent just read, and the
 * finished answer opens its first citation — so the right half is never the
 * dead space it used to be before the first click.
 */

import { useQuery } from "@tanstack/react-query";
import { MessageSquarePlus, Square } from "lucide-react";
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
  /** True once the viewer picks a citation: auto-follow yields to them. */
  const pinnedRef = useRef(false);
  /** True while the transcript is scrolled near its tail. */
  const stickRef = useRef(true);

  // Below `lg` the viewer is a full-screen sheet, not a side pane. Opening one
  // of those unasked hijacks the whole page, so auto-follow is a wide-viewport
  // behaviour only; on a narrow screen the citation chips still open it on tap.
  const [isWide, setIsWide] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia("(min-width: 1024px)");
    const sync = () => setIsWide(mq.matches);
    sync();
    mq.addEventListener("change", sync);
    return () => mq.removeEventListener("change", sync);
  }, []);

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

  // Auto-follow, part 1: while tools run, show the file the agent just read.
  const liveTarget = (() => {
    const ex = chat.current;
    if (!ex) return null;
    for (let i = ex.steps.length - 1; i >= 0; i--) {
      const loc = ex.steps[i].locations?.[0];
      if (loc) return loc;
    }
    return null;
  })();
  const liveTargetKey = liveTarget ? citationKey(liveTarget) : null;
  const liveTargetRef = useRef<Citation | null>(null);
  liveTargetRef.current = liveTarget;
  useEffect(() => {
    if (!isWide || pinnedRef.current) return;
    const target = liveTargetRef.current;
    if (target) setSelection(target);
  }, [liveTargetKey, isWide]);

  // Auto-follow, part 2: a finished answer opens its first validated citation.
  const settled = chat.transcript[chat.transcript.length - 1] ?? null;
  const settledCitation = settled?.citations[0] ?? null;
  const settledKey = settledCitation ? citationKey(settledCitation) : null;
  const settledRef = useRef<Citation | null>(null);
  settledRef.current = settledCitation;
  useEffect(() => {
    if (!isWide || pinnedRef.current) return;
    const target = settledRef.current;
    if (target) setSelection(target);
  }, [settledKey, isWide]);

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
    pinnedRef.current = false;
    stickRef.current = true;
    chat.ask(q);
    setQuestion("");
  };

  const pick = (c: Citation) => {
    pinnedRef.current = true;
    setSelection(c);
  };

  const newConversation = () => {
    chat.clear();
    pinnedRef.current = false;
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
      {/* Left — conversation */}
      <section className="flex min-h-0 flex-1 flex-col lg:border-r">
        <div className="flex items-center justify-between gap-3 border-b px-4 py-2">
          <div className="flex min-w-0 items-baseline gap-2">
            <span className="truncate font-mono text-sm font-medium">
              {repo.data.name}
            </span>
            <span className="hidden shrink-0 text-xs text-muted-foreground sm:inline">
              {progress.files_total} files · {progress.chunks_total} chunks
            </span>
          </div>
          <div className="flex shrink-0 items-center gap-1">
            {chat.transcript.length > 0 && (
              <button
                type="button"
                onClick={newConversation}
                className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              >
                <MessageSquarePlus className="size-3.5" />
                New chat
              </button>
            )}
            <Link
              href={`/repos/${repoId}`}
              className="rounded-md px-2 py-1 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              repo status
            </Link>
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
          {empty && (
            <div className="pt-10 text-center">
              <p className="text-sm font-medium">Ask about this codebase</p>
              <p className="mx-auto mt-2 max-w-sm text-sm text-muted-foreground">
                The agent&apos;s tool calls stream here as it explores, and the
                code it cites opens on the right.
              </p>
              <div className="mt-5 flex flex-wrap justify-center gap-2">
                {SUGGESTIONS.map((s) => (
                  <button
                    key={s}
                    type="button"
                    onClick={() => send(s)}
                    className="rounded-full border bg-card px-3 py-1.5 text-xs text-muted-foreground shadow-sm transition-colors hover:border-primary/30 hover:text-foreground"
                  >
                    {s}
                  </button>
                ))}
              </div>
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
              className="flex max-h-40 min-h-[42px] w-full resize-none rounded-xl border border-input bg-card px-3 py-2.5 text-sm shadow-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:cursor-not-allowed disabled:opacity-50"
            />
            {streaming ? (
              <Button
                type="button"
                variant="outline"
                onClick={chat.stop}
                className="h-[42px] shrink-0 rounded-xl"
              >
                <Square className="size-3.5 fill-current" />
                Stop
              </Button>
            ) : (
              <Button type="submit" className="h-[42px] shrink-0 rounded-xl px-5">
                Ask
              </Button>
            )}
          </div>
          <p className="mt-1.5 px-1 text-[11px] text-muted-foreground">
            Enter to send · Shift+Enter for a new line · the agent stops after 8
            tool calls
          </p>
        </form>
      </section>

      {/* Right — code viewer. A sheet below lg, a pane at lg and up. */}
      <section
        className={cn(
          "fixed inset-x-0 bottom-0 top-12 z-50 border-t bg-card shadow-2xl transition-transform duration-200",
          selection ? "translate-y-0" : "translate-y-full",
          "lg:static lg:z-auto lg:w-[44%] lg:translate-y-0 lg:border-t-0 lg:shadow-none",
        )}
      >
        <CodeViewer
          repoId={repoId}
          selection={selection}
          repoUrl={repo.data.url}
          headSha={repo.data.head_sha}
          onClose={() => {
            pinnedRef.current = true;
            setSelection(null);
          }}
        />
      </section>
    </div>
  );
}
