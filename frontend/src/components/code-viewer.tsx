"use client";

/**
 * Right pane: Shiki-rendered file viewer with line-range highlighting.
 *
 * Content comes from `GET /repos/{id}/files` via TanStack Query (cached per
 * repo+path). Each line is its own element with `data-line`, so citation
 * clicks can scroll to the start line and mark the 1-based inclusive range.
 *
 * Emphasis is a gutter accent, not a background wash: a citation can legally
 * span an entire 215-line file, and washing every line of it highlights
 * nothing. The accent scales; the start line always stays findable.
 */

import { useQuery } from "@tanstack/react-query";
import { Check, Copy, ExternalLink, WrapText, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { ThemedToken } from "shiki/core";

import { Skeleton } from "@/components/ui/skeleton";
import { ApiError, getFile } from "@/lib/api";
import type { Citation } from "@/lib/citations";
import { githubBlobUrl } from "@/lib/format";
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

function IconButton({
  onClick,
  label,
  active,
  icon: Icon,
}: {
  onClick: () => void;
  label: string;
  active?: boolean;
  icon: typeof Copy;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={label}
      aria-label={label}
      aria-pressed={active}
      className={cn(
        "rounded-md p-1.5 transition-colors hover:bg-muted hover:text-foreground",
        active ? "bg-muted text-foreground" : "text-muted-foreground",
      )}
    >
      <Icon className="size-3.5" />
    </button>
  );
}

function Placeholder({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-full items-center justify-center p-8 text-center text-sm text-muted-foreground">
      {children}
    </div>
  );
}

export function CodeViewer({
  repoId,
  selection,
  repoUrl,
  headSha,
  onClose,
}: {
  repoId: string;
  /** The citation to show; null renders the empty state. */
  selection: Citation | null;
  repoUrl?: string;
  headSha?: string | null;
  /** Mobile only — the viewer is a sheet there and needs a dismiss. */
  onClose?: () => void;
}) {
  // Wrapping defaults on where the pane is narrow — a 44% column or a phone
  // sheet otherwise makes reading any real line a horizontal scroll. Set once
  // after mount (not in the initial state, which would differ from SSR); a
  // manual toggle wins from then on.
  const [wrap, setWrap] = useState(false);
  useEffect(() => {
    setWrap(!window.matchMedia("(min-width: 1024px)").matches);
  }, []);
  const [copied, setCopied] = useState(false);
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
      <Placeholder>
        Click a citation or a step location to view the cited code here.
      </Placeholder>
    );
  }

  if (file.isError) {
    const notFound = file.error instanceof ApiError && file.error.status === 404;
    return (
      <Placeholder>
        {notFound
          ? `“${selection.file_path}” is not in this repo's index.`
          : "Could not load the file — is the backend running?"}
      </Placeholder>
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

  const content = file.data.content;
  const inRange = (line: number) =>
    line >= selection.start_line && line <= selection.end_line;

  const copyRange = () => {
    const lines = content.split("\n");
    const snippet = lines
      .slice(selection.start_line - 1, selection.end_line)
      .join("\n");
    void navigator.clipboard.writeText(snippet).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    });
  };

  const blobUrl = githubBlobUrl(repoUrl, headSha, selection);

  return (
    <div className="flex h-full flex-col bg-card">
      <div className="flex items-center gap-2 border-b px-3 py-2">
        <span className="truncate font-mono text-xs font-medium">
          {file.data.path}
        </span>
        <span className="shrink-0 rounded border bg-muted px-1.5 py-px font-mono text-[10px] tabular-nums text-muted-foreground">
          L{selection.start_line}–{selection.end_line}
        </span>
        <div className="ml-auto flex shrink-0 items-center gap-0.5">
          <IconButton
            onClick={() => setWrap((v) => !v)}
            label={wrap ? "Disable line wrap" : "Wrap long lines"}
            active={wrap}
            icon={WrapText}
          />
          <IconButton
            onClick={copyRange}
            label="Copy the cited lines"
            icon={copied ? Check : Copy}
          />
          {blobUrl && (
            <a
              href={blobUrl}
              target="_blank"
              rel="noreferrer"
              title="Open on GitHub"
              aria-label="Open on GitHub"
              className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              <ExternalLink className="size-3.5" />
            </a>
          )}
          {onClose && (
            <button
              type="button"
              onClick={onClose}
              aria-label="Close the code viewer"
              className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground lg:hidden"
            >
              <X className="size-4" />
            </button>
          )}
        </div>
      </div>

      <div ref={scrollRef} className="min-h-0 flex-1 overflow-auto">
        <pre
          className={cn(
            "min-w-full py-3 text-[13px] leading-relaxed",
            wrap ? "w-full" : "w-max",
          )}
        >
          {tokens.map((lineTokens, i) => {
            const line = i + 1; // tokens are 0-indexed; file lines are 1-based
            const marked = inRange(line);
            const isStart = line === selection.start_line;
            return (
              <div
                key={line}
                data-line={line}
                className={cn(
                  "flex",
                  marked && "bg-primary/[0.06]",
                  isStart && "bg-primary/[0.12]",
                )}
              >
                <span
                  aria-hidden
                  className={cn(
                    "sticky left-0 w-12 shrink-0 select-none border-r-2 bg-card pr-2.5 text-right tabular-nums",
                    marked
                      ? "border-primary text-primary"
                      : "border-transparent text-muted-foreground/50",
                    isStart && "font-semibold",
                  )}
                >
                  {line}
                </span>
                <code
                  className={cn(
                    "pl-3 pr-4",
                    // min-w-0 is what lets a flex child shrink below its
                    // content; without it wrapping cannot take effect at all.
                    wrap && "min-w-0 flex-1 whitespace-pre-wrap break-all",
                  )}
                >
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
