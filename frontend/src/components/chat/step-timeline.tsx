"use client";

/**
 * The hero element: the agent's tool calls streaming in as they happen.
 *
 * A step appears on `tool_call` (spinner, args) and completes in place on
 * `tool_result` (summary + location chips). Location chips carry no code —
 * clicking one fetches the range via `/files` into the viewer (§9).
 *
 * A finished timeline collapses to a one-line trace so a long transcript stays
 * readable; a live one is always expanded, because watching it is the point.
 */

import { ChevronDown, FileText, FolderTree, Expand, Network, Search } from "lucide-react";
import { useState } from "react";

import { CitationChip } from "@/components/chat/citation-chip";
import { type Citation, citationKey } from "@/lib/citations";
import type { ChatStep } from "@/lib/chat-types";
import { cn } from "@/lib/utils";

const MAX_CHIPS_PER_STEP = 5;

/** The six agent tools (CLAUDE.md); anything unknown falls back to search. */
const TOOL_ICONS: Record<string, typeof Search> = {
  search_code: Search,
  read_file: FileText,
  get_definition: Network,
  find_references: Network,
  expand_context: Expand,
  list_directory: FolderTree,
};

function argsSummary(args: Record<string, unknown>): string {
  const parts = Object.entries(args)
    .filter(([, v]) => v !== null && v !== undefined && v !== "")
    .map(([k, v]) => `${k}=${typeof v === "string" ? `"${v}"` : String(v)}`);
  const joined = parts.join(", ");
  return joined.length > 90 ? `${joined.slice(0, 87)}…` : joined;
}

function formatMs(ms: number): string {
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`;
}

/**
 * `read_file` reports its summary as exactly `path:start-end`, which is also
 * its single location chip (backend `summarize_tool_result`). Rendering both is
 * the same fact twice.
 */
function summaryIsChip(step: ChatStep): boolean {
  return (
    step.locations?.length === 1 &&
    step.summary === citationKey(step.locations[0])
  );
}

function StepItem({
  step,
  isLast,
  onCiteClick,
  activeKey,
}: {
  step: ChatStep;
  isLast: boolean;
  onCiteClick: (c: Citation) => void;
  activeKey: string | null;
}) {
  const pending = step.summary === undefined;
  const Icon = TOOL_ICONS[step.tool] ?? Search;

  return (
    <li className="relative flex gap-3 pb-4">
      {!isLast && (
        <span
          aria-hidden
          className="absolute left-[13px] top-7 h-full w-px bg-border"
        />
      )}
      <span
        className={cn(
          "z-10 mt-0.5 flex size-[27px] shrink-0 items-center justify-center rounded-lg border",
          pending
            ? "animate-pulse border-primary/30 bg-primary/10 text-primary"
            : "border-border bg-card text-muted-foreground",
        )}
      >
        <Icon className="size-3.5" />
      </span>

      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex flex-wrap items-baseline gap-x-2">
          <span className="font-mono text-xs font-semibold">{step.tool}</span>
          <span className="truncate font-mono text-[11px] text-muted-foreground">
            {argsSummary(step.args)}
          </span>
          {step.ms != null && (
            <span className="ml-auto shrink-0 font-mono text-[10px] tabular-nums text-muted-foreground/70">
              {formatMs(step.ms)}
            </span>
          )}
        </div>

        {pending ? (
          <p className="text-xs text-muted-foreground">running…</p>
        ) : (
          <>
            {!summaryIsChip(step) && (
              <p className="text-xs text-muted-foreground">{step.summary}</p>
            )}
            {step.locations && step.locations.length > 0 && (
              <div className="flex flex-wrap gap-1 pt-0.5">
                {step.locations.slice(0, MAX_CHIPS_PER_STEP).map((loc, j) => (
                  <CitationChip
                    key={j}
                    citation={loc}
                    onClick={onCiteClick}
                    active={activeKey === citationKey(loc)}
                  />
                ))}
                {step.locations.length > MAX_CHIPS_PER_STEP && (
                  <span className="self-center text-[11px] text-muted-foreground">
                    +{step.locations.length - MAX_CHIPS_PER_STEP} more
                  </span>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </li>
  );
}

export function StepTimeline({
  steps,
  live,
  onCiteClick,
  activeKey,
}: {
  steps: ChatStep[];
  /** Live timelines never collapse — watching the agent work is the feature. */
  live?: boolean;
  onCiteClick: (c: Citation) => void;
  activeKey: string | null;
}) {
  const [open, setOpen] = useState(false);
  if (steps.length === 0) return null;

  const expanded = live || open;
  const totalMs = steps.reduce((sum, s) => sum + (s.ms ?? 0), 0);

  return (
    <div className="rounded-xl border bg-card/60 p-3">
      {!live && (
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={expanded}
          className="flex w-full items-center gap-2 text-left text-xs text-muted-foreground transition-colors hover:text-foreground"
        >
          <ChevronDown
            className={cn(
              "size-3.5 shrink-0 transition-transform",
              expanded && "rotate-180",
            )}
          />
          <span className="font-medium">
            {steps.length} tool call{steps.length === 1 ? "" : "s"}
          </span>
          <span className="truncate font-mono text-[11px]">
            {steps.map((s) => s.tool).join(" → ")}
          </span>
          {totalMs > 0 && (
            <span className="ml-auto shrink-0 font-mono text-[10px] tabular-nums">
              {formatMs(totalMs)}
            </span>
          )}
        </button>
      )}

      {expanded && (
        <ol className={cn("space-y-0", !live && "mt-3 border-t pt-3")}>
          {steps.map((step, i) => (
            <StepItem
              key={step.n}
              step={step}
              isLast={i === steps.length - 1}
              onCiteClick={onCiteClick}
              activeKey={activeKey}
            />
          ))}
        </ol>
      )}
    </div>
  );
}
