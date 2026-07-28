"use client";

/**
 * One question + everything its stream produced: step timeline, answer prose
 * with inline citation chips, the validated `citations` chips, and errors.
 *
 * The assistant turn is one column under an avatar so it reads as a reply
 * rather than as loose text under the question bubble.
 */

import { Check, Copy, RotateCcw } from "lucide-react";
import { useMemo, useState } from "react";

import { AnswerBody } from "@/components/chat/answer-body";
import { CitationChip } from "@/components/chat/citation-chip";
import { StepTimeline } from "@/components/chat/step-timeline";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { type Citation, citationKey, dedupeCitations } from "@/lib/citations";
import type { ChatExchange, ChatStatus } from "@/lib/chat-types";
import { inlineCitationKeys, parseMarkdown } from "@/lib/markdown";

function AssistantAvatar() {
  return (
    <span
      aria-hidden
      className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground"
    >
      <svg
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.25"
        strokeLinecap="round"
        className="size-4"
      >
        <path d="M7 7h4a4 4 0 0 1 4 4v2a4 4 0 0 0 4 4h1" />
        <circle cx="5" cy="7" r="2" fill="currentColor" stroke="none" />
        <circle cx="5" cy="17" r="2" fill="currentColor" stroke="none" />
        <path d="M7 17h4" />
      </svg>
    </span>
  );
}

function Working({ label }: { label: string }) {
  return (
    <p
      aria-live="polite"
      className="flex items-center gap-2 text-sm text-muted-foreground"
    >
      <span className="size-2 animate-pulse rounded-full bg-primary" />
      {label}
    </p>
  );
}

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
}: {
  exchange: ChatExchange;
  /** True while this exchange is still streaming. */
  live?: boolean;
  status?: ChatStatus;
  onCiteClick: (c: Citation) => void;
  activeKey: string | null;
  onRegenerate?: (question: string) => void;
}) {
  const [copied, setCopied] = useState(false);

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
    void navigator.clipboard.writeText(exchange.answer).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    });
  };

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-2xl rounded-br-md bg-primary px-3.5 py-2 text-sm text-primary-foreground shadow-sm">
          {exchange.question}
        </div>
      </div>

      <div className="flex gap-3">
        <AssistantAvatar />
        <div className="min-w-0 flex-1 space-y-3">
          {live && status === "thinking" && exchange.steps.length === 0 && (
            <Working label="thinking…" />
          )}

          <StepTimeline
            steps={exchange.steps}
            live={live}
            onCiteClick={onCiteClick}
            activeKey={activeKey}
          />

          {live && status === "composing" && !exchange.answer && (
            <Working label="writing answer…" />
          )}

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
              <p className="text-[11px] font-medium text-muted-foreground">
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
    </div>
  );
}
