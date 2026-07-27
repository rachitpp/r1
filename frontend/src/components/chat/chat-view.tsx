"use client";

/**
 * `/repos/[id]/chat` — the split pane. Conversation left, code viewer right;
 * stacked on narrow viewports. Owns the "which citation is open" state that
 * ties the two panes together.
 */

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { ExchangeView } from "@/components/chat/exchange-view";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { CodeViewer } from "@/components/code-viewer";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useRepoChat } from "@/hooks/use-repo-chat";
import { ApiError, getRepo } from "@/lib/api";
import { type Citation, citationKey } from "@/lib/citations";

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
    if (streaming) bottomRef.current?.scrollIntoView({ block: "end" });
  }, [streaming, liveSize]);

  // 409 mid-session: the repo regressed to not-ready (e.g. a re-ingest).
  // The status page is the right place to wait it out.
  useEffect(() => {
    if (chat.notReadyStatus) router.push(`/repos/${repoId}`);
  }, [chat.notReadyStatus, router, repoId]);

  if (repo.isPending) {
    return (
      <div className="mx-auto max-w-3xl space-y-4 px-4 py-10">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }
  if (repo.isError) {
    const notFound = repo.error instanceof ApiError && repo.error.status === 404;
    return (
      <div className="mx-auto max-w-3xl px-4 py-10">
        <Alert variant="destructive">
          <AlertTitle>
            {notFound ? "Repository not found" : "Error"}
          </AlertTitle>
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
      <div className="mx-auto max-w-3xl px-4 py-10">
        <Alert>
          <AlertTitle>This repository is not ready yet</AlertTitle>
          <AlertDescription className="space-y-3">
            <p>
              Current status: <span className="font-mono">{repo.data.status}</span>
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

  return (
    <div className="mx-auto flex h-[calc(100vh-3rem)] max-w-7xl flex-col lg:flex-row">
      {/* Left — conversation */}
      <section className="flex min-h-0 flex-1 flex-col lg:border-r">
        <div className="flex items-center justify-between gap-2 border-b px-4 py-2">
          <div className="min-w-0">
            <span className="truncate font-mono text-sm font-medium">
              {repo.data.name}
            </span>
          </div>
          <Link
            href={`/repos/${repoId}`}
            className="shrink-0 text-xs text-muted-foreground hover:underline"
          >
            repo status
          </Link>
        </div>

        <div className="min-h-0 flex-1 space-y-8 overflow-y-auto px-4 py-6">
          {empty && (
            <div className="pt-12 text-center text-sm text-muted-foreground">
              <p className="font-medium text-foreground">
                Ask about this codebase
              </p>
              <p className="mx-auto mt-2 max-w-sm">
                Try “How does authentication work?” or name a class or function.
                The agent&apos;s tool calls stream here as it explores.
              </p>
            </div>
          )}
          {chat.transcript.map((exchange, i) => (
            <ExchangeView
              key={i}
              exchange={exchange}
              onCiteClick={setSelection}
              activeKey={activeKey}
            />
          ))}
          {chat.current && (
            <ExchangeView
              exchange={chat.current}
              live
              status={chat.status}
              onCiteClick={setSelection}
              activeKey={activeKey}
            />
          )}
          <div ref={bottomRef} />
        </div>

        <form
          className="flex gap-2 border-t p-3"
          onSubmit={(e) => {
            e.preventDefault();
            const q = question.trim();
            if (!q || streaming) return;
            chat.ask(q);
            setQuestion("");
          }}
        >
          <Input
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder={
              streaming ? "Waiting for the answer…" : "Ask a question…"
            }
            disabled={streaming}
            aria-label="Question"
          />
          <Button type="submit" disabled={streaming || !question.trim()}>
            Ask
          </Button>
        </form>
      </section>

      {/* Right — code viewer */}
      <section className="h-80 min-h-0 border-t lg:h-auto lg:w-[44%] lg:border-t-0">
        <CodeViewer repoId={repoId} selection={selection} />
      </section>
    </div>
  );
}
