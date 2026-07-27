"use client";

/**
 * The hero element: the agent's tool calls streaming in as they happen.
 *
 * A step appears on `tool_call` (spinner, args) and completes in place on
 * `tool_result` (summary + location chips). Location chips carry no code —
 * clicking one fetches the range via `/files` into the viewer (§9).
 */

import { CitationChip } from "@/components/chat/citation-chip";
import type { Citation } from "@/lib/citations";
import type { ChatStep } from "@/lib/chat-types";

const MAX_CHIPS_PER_STEP = 5;

function argsSummary(args: Record<string, unknown>): string {
  const parts = Object.entries(args)
    .filter(([, v]) => v !== null && v !== undefined && v !== "")
    .map(([k, v]) => `${k}=${typeof v === "string" ? `"${v}"` : String(v)}`);
  const joined = parts.join(", ");
  return joined.length > 90 ? `${joined.slice(0, 87)}…` : joined;
}

export function StepTimeline({
  steps,
  onCiteClick,
  activeKey,
}: {
  steps: ChatStep[];
  onCiteClick: (c: Citation) => void;
  activeKey: string | null;
}) {
  if (steps.length === 0) return null;
  return (
    <ol className="space-y-0">
      {steps.map((step, i) => {
        const pending = step.summary === undefined;
        const isLast = i === steps.length - 1;
        return (
          <li key={step.n} className="relative flex gap-3 pb-4">
            {/* connector line */}
            {!isLast && (
              <span
                aria-hidden
                className="absolute left-[11px] top-6 h-full w-px bg-border"
              />
            )}
            <span
              className={
                "z-10 mt-0.5 flex size-[23px] shrink-0 items-center justify-center rounded-full border text-[11px] font-semibold " +
                (pending
                  ? "border-blue-300 bg-blue-50 text-blue-700"
                  : "border-border bg-muted text-muted-foreground")
              }
            >
              {pending ? (
                <span className="size-2 animate-pulse rounded-full bg-blue-500" />
              ) : (
                step.n
              )}
            </span>
            <div className="min-w-0 flex-1 space-y-1">
              <div className="flex flex-wrap items-baseline gap-x-2">
                <span className="font-mono text-xs font-semibold">
                  {step.tool}
                </span>
                <span className="truncate font-mono text-[11px] text-muted-foreground">
                  {argsSummary(step.args)}
                </span>
              </div>
              {pending ? (
                <p className="text-xs text-muted-foreground">running…</p>
              ) : (
                <>
                  <p className="text-xs text-muted-foreground">{step.summary}</p>
                  {step.locations && step.locations.length > 0 && (
                    <div className="flex flex-wrap gap-1 pt-0.5">
                      {step.locations
                        .slice(0, MAX_CHIPS_PER_STEP)
                        .map((loc, j) => (
                          <CitationChip
                            key={j}
                            citation={loc}
                            onClick={onCiteClick}
                            active={
                              activeKey ===
                              `${loc.file_path}:${loc.start_line}-${loc.end_line}`
                            }
                          />
                        ))}
                      {step.locations.length > MAX_CHIPS_PER_STEP && (
                        <span className="text-[11px] text-muted-foreground">
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
      })}
    </ol>
  );
}
