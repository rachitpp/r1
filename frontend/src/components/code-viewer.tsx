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

/**
 * The resting state, drawn as a ghost of the viewer itself: a stack of blank
 * code lines with a cited range marked in clay exactly the way a real citation
 * is marked. It shows what a click will do instead of describing it, and it
 * fills a pane that is otherwise 44% of the screen holding one grey sentence.
 *
 * Indents and widths are hand-set to read as plausible Python — a uniform
 * ladder of equal bars reads as a loading skeleton, which this is not.
 */
const GHOST_LINES: { indent: number; width: number; marked?: boolean }[] = [
  { indent: 0, width: 62 },
  { indent: 1, width: 44 },
  { indent: 0, width: 0 },
  { indent: 1, width: 78, marked: true },
  { indent: 2, width: 55, marked: true },
  { indent: 2, width: 68, marked: true },
  { indent: 1, width: 30, marked: true },
  { indent: 0, width: 0 },
  { indent: 1, width: 50 },
  { indent: 2, width: 36 },
];

function EmptyState() {
  return (
    <div className="flex h-full items-center justify-center overflow-hidden p-8">
      <div className="w-full max-w-[19rem]">
        <p className="eyebrow">Source</p>

        <h2 className="display mt-4 text-xl font-semibold leading-snug">
          Nothing open yet.
        </h2>

        <p className="mt-2.5 text-[13px] leading-relaxed text-muted-foreground">
          Click any{" "}
          <span className="rounded-sm border bg-muted/60 px-1 py-px font-mono text-[11px] text-foreground/80">
            file:line
          </span>{" "}
          chip — in an answer or in the tool trace — and the source opens here,
          scrolled to the cited lines.
        </p>

        <div aria-hidden className="mt-7 select-none">
          {GHOST_LINES.map((line, i) => (
            <div
              key={i}
              className={cn(
                "flex items-center gap-3 py-[3px] pl-2",
                line.marked && "border-l-2 border-primary bg-primary/[0.05]",
                !line.marked && "border-l-2 border-transparent",
              )}
            >
              <span className="w-5 shrink-0 text-right font-mono text-[10px] tabular-nums text-muted-foreground/35">
                {241 + i}
              </span>
              {line.width > 0 && (
                <span
                  className={cn(
                    "h-[5px] rounded-full",
                    line.marked ? "bg-primary/25" : "bg-foreground/[0.09]",
                  )}
                  style={{
                    width: `${line.width}%`,
                    marginLeft: `${line.indent * 12}px`,
                  }}
                />
              )}
            </div>
          ))}
        </div>
      </div>
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

  if (!selection) return <EmptyState />;

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
        {/* 11.5px, not 13px: this pane is a ~44% column, and at 13px a typical
            88-column Python line does not fit — every line became a horizontal
            scroll or a wrap. The smaller size buys roughly ten more columns. */}
        <pre
          className={cn(
            "min-w-full py-3 text-[11.5px] leading-[1.65]",
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
                    "sticky left-0 w-10 shrink-0 select-none border-r-2 bg-card pr-2 text-right tabular-nums",
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
                    "pl-2.5 pr-4",
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
