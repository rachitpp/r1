"use client";

/**
 * One question + everything its stream produced: step timeline, answer text
 * with inline citation chips, the validated `citations` chips, and errors.
 */

import { CitationChip } from "@/components/chat/citation-chip";
import { StepTimeline } from "@/components/chat/step-timeline";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import {
  type Citation,
  citationKey,
  dedupeCitations,
  segmentAnswer,
} from "@/lib/citations";
import type { ChatExchange, ChatStatus } from "@/lib/chat-types";

function AnswerText({
  text,
  onCiteClick,
  activeKey,
}: {
  text: string;
  onCiteClick: (c: Citation) => void;
  activeKey: string | null;
}) {
  return (
    <div className="whitespace-pre-wrap text-sm leading-relaxed">
      {segmentAnswer(text).map((segment, i) =>
        segment.kind === "text" ? (
          <span key={i}>{segment.text}</span>
        ) : (
          <CitationChip
            key={i}
            citation={segment.citation}
            onClick={onCiteClick}
            active={activeKey === citationKey(segment.citation)}
          />
        ),
      )}
    </div>
  );
}

export function ExchangeView({
  exchange,
  live,
  status,
  onCiteClick,
  activeKey,
}: {
  exchange: ChatExchange;
  /** True while this exchange is still streaming. */
  live?: boolean;
  status?: ChatStatus;
  onCiteClick: (c: Citation) => void;
  activeKey: string | null;
}) {
  // The citations event is backend-validated; text markers fill in while the
  // event hasn't arrived yet (streaming) and then dedupe against it.
  const chips = dedupeCitations(exchange.citations);

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground">
          {exchange.question}
        </div>
      </div>

      {live && status === "thinking" && exchange.steps.length === 0 && (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <span className="size-2 animate-pulse rounded-full bg-blue-500" />
          thinking…
        </p>
      )}

      <StepTimeline
        steps={exchange.steps}
        onCiteClick={onCiteClick}
        activeKey={activeKey}
      />

      {live && status === "composing" && !exchange.answer && (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <span className="size-2 animate-pulse rounded-full bg-blue-500" />
          writing answer…
        </p>
      )}

      {exchange.answer && (
        <AnswerText
          text={exchange.answer}
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

      {chips.length > 0 && (
        <div className="flex flex-wrap gap-1 border-t pt-3">
          {chips.map((c) => (
            <CitationChip
              key={citationKey(c)}
              citation={c}
              onClick={onCiteClick}
              active={activeKey === citationKey(c)}
            />
          ))}
        </div>
      )}

      {exchange.toolCallsUsed != null && (
        <p className="text-[11px] text-muted-foreground">
          {exchange.toolCallsUsed} tool call
          {exchange.toolCallsUsed === 1 ? "" : "s"}
        </p>
      )}

      {exchange.error && (
        <Alert variant="destructive">
          <AlertTitle>Something went wrong</AlertTitle>
          <AlertDescription className="text-xs">
            {exchange.error}
          </AlertDescription>
        </Alert>
      )}
    </div>
  );
}
