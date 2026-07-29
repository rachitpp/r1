"use client";

/**
 * The hero element: the agent's tool calls streaming in as they happen.
 *
 * A step appears on `tool_call` (spinner, args) and completes in place on
 * `tool_result` (summary + location chips). Location chips carry no code —
 * clicking one fetches the range via `/files` into the viewer (§9).
 *
 * Collapsed by default in both states, live and finished.
 *
 * A live run used to render every step expanded — "watching it is the point" —
 * but eight tool calls then grew a tall stack that pushed the question off
 * screen and shifted the answer down as it arrived. So the live view is one
 * fixed-height row that mutates in place (the phase, then the tool currently
 * running, then the next), with the whole trace one click away. Nothing about
 * what streams changes; only how much room it takes while it does.
 *
 * The two graph tools are inked differently from the four retrieval/file tools.
 * That is not decoration: "the graph reaches what retrieval could not" is the
 * project's whole claim, and this is the only place a reader can watch it
 * happen. The tool name is always spelled out beside the node, so colour is
 * reinforcement rather than the sole carrier of the distinction.
 */

import {
  ChevronDown,
  Crosshair,
  Expand,
  FileText,
  FolderTree,
  Search,
  Waypoints,
} from "lucide-react";
import { useState } from "react";

import { CitationChip } from "@/components/chat/citation-chip";
import { type Citation, citationKey } from "@/lib/citations";
import type { ChatStatus, ChatStep } from "@/lib/chat-types";
import { cn } from "@/lib/utils";

const MAX_CHIPS_PER_STEP = 5;

/** The agent loop's hard cap (CLAUDE.md §6). Surfaced in the header so the
 * budget is visible while it is being spent, rather than buried in the
 * composer's keyboard-shortcut line where it meant nothing. */
const MAX_TOOL_CALLS = 8;

/** The six agent tools (CLAUDE.md); anything unknown falls back to search. */
const TOOL_ICONS: Record<string, typeof Search> = {
  search_code: Search,
  read_file: FileText,
  get_definition: Crosshair,
  find_references: Waypoints,
  expand_context: Expand,
  list_directory: FolderTree,
};

/** The tools that walk the symbol graph rather than the index. */
const GRAPH_TOOLS = new Set(["get_definition", "find_references"]);

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
  index,
  isLast,
  onCiteClick,
  activeKey,
}: {
  step: ChatStep;
  index: number;
  isLast: boolean;
  onCiteClick: (c: Citation) => void;
  activeKey: string | null;
}) {
  const pending = step.summary === undefined;
  const Icon = TOOL_ICONS[step.tool] ?? Search;
  const isGraph = GRAPH_TOOLS.has(step.tool);

  return (
    <li className="relative flex gap-3 pb-3.5 last:pb-0">
      {/* The spine, starting below the step number (node 27px + gap 4px +
          number ~13px) so the line does not run through the digits. */}
      {!isLast && (
        <span
          aria-hidden
          className="absolute left-[13px] top-11 h-full w-px bg-border"
        />
      )}

      <span className="relative z-10 flex shrink-0 flex-col items-center gap-1">
        <span
          className={cn(
            "flex size-[27px] items-center justify-center rounded-md border bg-card transition-colors",
            pending
              ? "animate-pulse border-primary/40 bg-primary/10 text-primary"
              : isGraph
                ? "border-[hsl(var(--ochre)/0.45)] bg-[hsl(var(--ochre)/0.1)] text-[hsl(var(--ochre))]"
                : "border-border text-muted-foreground",
          )}
        >
          <Icon className="size-3.5" />
        </span>
        <span className="select-none font-mono text-[9px] tabular-nums text-muted-foreground/50">
          {String(index + 1).padStart(2, "0")}
        </span>
      </span>

      <div className="min-w-0 flex-1 space-y-1 pt-0.5">
        <div className="flex flex-wrap items-baseline gap-x-2">
          <span
            className={cn(
              "font-mono text-xs font-semibold",
              isGraph && "text-[hsl(var(--ochre))]",
            )}
          >
            {step.tool}
          </span>
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

/**
 * What the single live row says right now. Exactly one thing at a time, and it
 * replaces itself rather than appending — that is the whole point of the row.
 */
function liveLabel(
  steps: ChatStep[],
  status: ChatStatus | undefined,
): { text: string; mono: boolean } {
  if (status === "composing") return { text: "writing answer…", mono: false };
  if (status === "streaming")
    return { text: "answering…", mono: false };

  // A step with no summary yet is the one currently executing.
  const running = steps.find((s) => s.summary === undefined);
  if (running)
    return { text: `${running.tool}(${argsSummary(running.args)})`, mono: true };

  const last = steps[steps.length - 1];
  if (last) return { text: `${last.tool} ✓`, mono: true };
  return { text: "thinking…", mono: false };
}

export function StepTimeline({
  steps,
  live,
  status,
  onCiteClick,
  activeKey,
}: {
  steps: ChatStep[];
  /** True while this exchange is still streaming. */
  live?: boolean;
  status?: ChatStatus;
  onCiteClick: (c: Citation) => void;
  activeKey: string | null;
}) {
  const [open, setOpen] = useState(false);
  // A live run renders its row from the first moment, before any tool has been
  // called — that row is what replaced the separate "thinking…" line.
  if (steps.length === 0 && !live) return null;

  const totalMs = steps.reduce((sum, s) => sum + (s.ms ?? 0), 0);
  const graphCount = steps.filter((s) => GRAPH_TOOLS.has(s.tool)).length;
  const label = live ? liveLabel(steps, status) : null;

  return (
    <div className="rounded-md border bg-card/60">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-live={live ? "polite" : undefined}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-muted-foreground transition-colors hover:text-foreground"
      >
        {live ? (
          <span
            aria-hidden
            className="size-2 shrink-0 animate-pulse rounded-full bg-primary"
          />
        ) : (
          <ChevronDown
            className={cn(
              "size-3.5 shrink-0 transition-transform",
              open && "rotate-180",
            )}
          />
        )}

        <span className="shrink-0 font-medium tabular-nums">
          {steps.length}/{MAX_TOOL_CALLS} calls
        </span>

        {/* `truncate` + `min-w-0` is what keeps this row exactly one line high
            however long the running tool's arguments are. */}
        <span
          className={cn(
            "min-w-0 flex-1 truncate text-[11px]",
            (label?.mono ?? true) && "font-mono",
          )}
        >
          {label ? label.text : steps.map((s) => s.tool).join(" → ")}
        </span>

        {graphCount > 0 && (
          <span className="shrink-0 rounded-sm bg-[hsl(var(--ochre)/0.12)] px-1.5 py-px font-mono text-[10px] font-medium text-[hsl(var(--ochre))]">
            {graphCount} graph
          </span>
        )}
        {!live && totalMs > 0 && (
          <span className="shrink-0 font-mono text-[10px] tabular-nums">
            {formatMs(totalMs)}
          </span>
        )}
        {live && (
          <ChevronDown
            aria-hidden
            className={cn(
              "size-3.5 shrink-0 transition-transform",
              open && "rotate-180",
            )}
          />
        )}
      </button>

      {open && steps.length > 0 && (
        <ol className="border-t px-3 py-3">
          {steps.map((step, i) => (
            <StepItem
              key={step.n}
              step={step}
              index={i}
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
