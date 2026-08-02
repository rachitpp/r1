"use client";

/**
 * One question + everything its stream produced: step timeline, answer prose
 * with inline citation chips, the validated `citations` chips, and errors.
 *
 * Set as a notebook entry, not a chat thread: the question is a display-serif
 * heading behind a clay rule, and everything the turn produced hangs from that
 * rule's left edge. The right-aligned filled bubble and the assistant avatar
 * this used to render are the shape every generated chat UI has — and this is a
 * tool for reading code, not a messaging app.
 */

import { Check, Copy, Link2, RotateCcw } from "lucide-react";
import { useMemo, useState } from "react";

import { AnswerBody } from "@/components/chat/answer-body";
import { ApiError, shareAnswer } from "@/lib/api";
import { CitationChip } from "@/components/chat/citation-chip";
import { StepTimeline } from "@/components/chat/step-timeline";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { type Citation, citationKey, dedupeCitations } from "@/lib/citations";
import type { ChatExchange, ChatStatus } from "@/lib/chat-types";
import { inlineCitationKeys, parseMarkdown } from "@/lib/markdown";

function ActionButton({
  onClick,
  icon: Icon,
  children,
}: {
  onClick: () => void;
  icon: typeof Copy;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="inline-flex items-center gap-1.5 rounded-md px-1.5 py-1 text-[11px] font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
    >
      <Icon className="size-3.5" />
      {children}
    </button>
  );
}

export function ExchangeView({
  exchange,
  live,
  status,
  onCiteClick,
  activeKey,
  onRegenerate,
  repoId,
}: {
  exchange: ChatExchange;
  /** True while this exchange is still streaming. */
  live?: boolean;
  status?: ChatStatus;
  onCiteClick: (c: Citation) => void;
  activeKey: string | null;
  onRegenerate?: (question: string) => void;
  /** Enables Share. Omitted where there is nothing to publish against. */
  repoId?: string;
}) {
  const [copied, setCopied] = useState(false);
  const [share, setShare] = useState<
    { state: "idle" | "working" | "done" } | { state: "error"; message: string }
  >({ state: "idle" });

  const blocks = useMemo(
    () => parseMarkdown(exchange.answer),
    [exchange.answer],
  );

  // The citations event is backend-validated. Anything it names that the prose
  // already shows inline would be the same chip twice, so Sources lists only
  // what the answer text did not.
  const inlineKeys = useMemo(() => inlineCitationKeys(blocks), [blocks]);
  const extraSources = dedupeCitations(exchange.citations).filter(
    (c) => !inlineKeys.has(citationKey(c)),
  );

  const copyAnswer = () => {
    void navigator.clipboard
      ?.writeText(exchange.answer)
      .then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1600);
      })
      .catch(() => {
        // Clipboard unavailable (insecure origin) or permission denied: no-op.
      });
  };

  /**
   * Publish this answer and put its URL on the clipboard (SPEC §21.2).
   *
   * The link is built here, from the browser's own origin, because the API
   * returns an id and has no business knowing what host the frontend is served
   * from — the same reason §21.3 hands back facts rather than rendered HTML.
   */
  const publish = () => {
    if (!repoId || share.state === "working") return;
    setShare({ state: "working" });
    shareAnswer(repoId, {
      question: exchange.question,
      answer: exchange.answer,
      citations: exchange.citations,
    })
      .then(({ id }) => {
        const url = `${window.location.origin}/a/${id}`;
        setShare({ state: "done" });
        setTimeout(() => setShare({ state: "idle" }), 2400);
        return navigator.clipboard?.writeText(url);
      })
      .catch((err: unknown) => {
        setShare({
          state: "error",
          message:
            err instanceof ApiError ? err.message : "Could not create a link",
        });
        setTimeout(() => setShare({ state: "idle" }), 3200);
      });
  };

  return (
    <article className="space-y-4">
      {/* The clay rule runs the full height of the question and sets the left
          edge everything below hangs from — rule (2px) + gap (12px) = pl-3.5. */}
      <div className="flex gap-3">
        <span aria-hidden className="w-0.5 shrink-0 self-stretch bg-primary" />
        <h3 className="display min-w-0 text-lg font-semibold leading-snug">
          {exchange.question}
        </h3>
      </div>

      <div className="pl-3.5">
        <div className="min-w-0 space-y-3">
          {/* One status element, not three. "thinking…" and "writing answer…"
              used to be separate lines stacked around the timeline, so a live
              turn grew in three places at once; both phases are now labels on
              the single row below. */}
          <StepTimeline
            steps={exchange.steps}
            live={live}
            status={status}
            onCiteClick={onCiteClick}
            activeKey={activeKey}
          />

          {exchange.answer && (
            <AnswerBody
              blocks={blocks}
              onCiteClick={onCiteClick}
              activeKey={activeKey}
            />
          )}
          {live && status === "streaming" && (
            <span
              aria-hidden
              className="inline-block h-4 w-1.5 animate-pulse bg-foreground/60"
            />
          )}

          {extraSources.length > 0 && (
            <div className="space-y-1.5 border-t pt-3">
              <p className="font-mono text-[10px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
                Sources
              </p>
              <div className="flex flex-wrap gap-1">
                {extraSources.map((c) => (
                  <CitationChip
                    key={citationKey(c)}
                    citation={c}
                    onClick={onCiteClick}
                    active={activeKey === citationKey(c)}
                  />
                ))}
              </div>
            </div>
          )}

          {exchange.error && (
            <Alert variant="destructive">
              <AlertTitle>Something went wrong</AlertTitle>
              <AlertDescription className="text-xs">
                {exchange.error}
              </AlertDescription>
            </Alert>
          )}

          {!live && (exchange.answer || exchange.error) && (
            <div className="-ml-1.5 flex flex-wrap items-center gap-1">
              {exchange.answer && (
                <ActionButton
                  onClick={copyAnswer}
                  icon={copied ? Check : Copy}
                >
                  {copied ? "Copied" : "Copy"}
                </ActionButton>
              )}
              {onRegenerate && (
                <ActionButton
                  onClick={() => onRegenerate(exchange.question)}
                  icon={RotateCcw}
                >
                  Ask again
                </ActionButton>
              )}
              {repoId && exchange.answer && (
                <ActionButton
                  onClick={publish}
                  icon={share.state === "done" ? Check : Link2}
                >
                  {share.state === "working"
                    ? "Linking…"
                    : share.state === "done"
                      ? "Link copied"
                      : share.state === "error"
                        ? share.message
                        : "Share"}
                </ActionButton>
              )}
              {exchange.toolCallsUsed != null && (
                <span className="ml-1 text-[11px] text-muted-foreground">
                  {exchange.toolCallsUsed} tool call
                  {exchange.toolCallsUsed === 1 ? "" : "s"}
                </span>
              )}
              {exchange.stopped && (
                <span className="ml-1 text-[11px] text-muted-foreground">
                  · stopped
                </span>
              )}
            </div>
          )}
        </div>
      </div>
    </article>
  );
}
