"use client";

/**
 * Right pane: Shiki-rendered file viewer with line-range highlighting.
 *
 * Content comes from `GET /repos/{id}/files` via TanStack Query (cached per
 * repo+path). Each line is its own element with `data-line`, so citation
 * clicks can scroll to the start line and wash the 1-based inclusive range.
 */

import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import type { ThemedToken } from "shiki/core";

import { Skeleton } from "@/components/ui/skeleton";
import { ApiError, getFile } from "@/lib/api";
import type { Citation } from "@/lib/citations";
import { LIGHT_THEME, getHighlighter } from "@/lib/highlighter";
import { cn } from "@/lib/utils";

function useTokens(content: string | undefined): ThemedToken[][] | null {
  const [tokens, setTokens] = useState<ThemedToken[][] | null>(null);
  useEffect(() => {
    if (content == null) {
      setTokens(null);
      return;
    }
    let cancelled = false;
    void getHighlighter().then((h) => {
      if (cancelled) return;
      setTokens(
        h.codeToTokensBase(content, { lang: "python", theme: LIGHT_THEME }),
      );
    });
    return () => {
      cancelled = true;
    };
  }, [content]);
  return tokens;
}

export function CodeViewer({
  repoId,
  selection,
}: {
  repoId: string;
  /** The citation to show; null renders the empty state. */
  selection: Citation | null;
}) {
  const file = useQuery({
    queryKey: ["file", repoId, selection?.file_path],
    queryFn: () => getFile(repoId, selection!.file_path),
    enabled: selection != null,
    staleTime: Infinity, // file content is immutable for a given ingest
    retry: (count, err) =>
      !(err instanceof ApiError && err.status === 404) && count < 1,
  });
  const tokens = useTokens(file.data?.content);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Scroll once the cited file is tokenized; re-run when the range changes.
  useEffect(() => {
    if (!selection || !tokens || !scrollRef.current) return;
    const target = scrollRef.current.querySelector(
      `[data-line="${selection.start_line}"]`,
    );
    target?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [selection, tokens]);

  if (!selection) {
    return (
      <div className="flex h-full items-center justify-center p-8 text-center text-sm text-muted-foreground">
        Click a citation or a step location to view the cited code here.
      </div>
    );
  }

  if (file.isError) {
    const notFound = file.error instanceof ApiError && file.error.status === 404;
    return (
      <div className="flex h-full items-center justify-center p-8 text-center text-sm text-muted-foreground">
        {notFound
          ? `“${selection.file_path}” is not in this repo's index.`
          : "Could not load the file — is the backend running?"}
      </div>
    );
  }

  if (file.isPending || !tokens) {
    return (
      <div className="space-y-2 p-4">
        {[...Array(12)].map((_, i) => (
          <Skeleton key={i} className="h-4 w-full" />
        ))}
      </div>
    );
  }

  const inRange = (line: number) =>
    line >= selection.start_line && line <= selection.end_line;

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-baseline justify-between gap-2 border-b px-4 py-2">
        <span className="truncate font-mono text-xs font-medium">
          {file.data.path}
        </span>
        <span className="shrink-0 font-mono text-xs text-muted-foreground">
          L{selection.start_line}–{selection.end_line}
        </span>
      </div>
      <div ref={scrollRef} className="min-h-0 flex-1 overflow-auto">
        <pre className="w-max min-w-full py-3 text-[13px] leading-relaxed">
          {tokens.map((lineTokens, i) => {
            const line = i + 1; // tokens are 0-indexed; file lines are 1-based
            return (
              <div
                key={line}
                data-line={line}
                className={cn(
                  "flex px-0",
                  inRange(line) && "bg-amber-100/80",
                )}
              >
                <span
                  aria-hidden
                  className={cn(
                    "sticky left-0 w-12 shrink-0 select-none bg-background pr-3 text-right text-muted-foreground/50",
                    inRange(line) && "bg-amber-50 text-amber-700",
                  )}
                >
                  {line}
                </span>
                <code className="pr-4">
                  {lineTokens.length === 0
                    ? "​" // keep empty lines their full height
                    : lineTokens.map((t, j) => (
                        <span key={j} style={{ color: t.color }}>
                          {t.content}
                        </span>
                      ))}
                </code>
              </div>
            );
          })}
        </pre>
      </div>
    </div>
  );
}
