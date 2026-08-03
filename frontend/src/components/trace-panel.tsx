"use client";

/**
 * Call-hierarchy trace (SPEC §24, FEATURE-IDEAS 2.3).
 *
 * The graph views above answer questions about *modules*. This one follows a
 * single symbol outward — "what does this reach, eventually" — which is the
 * question the project's whole thesis is about and the one nothing else here
 * answered.
 *
 * Deliberately opt-in: it needs a symbol name, and a panel that guesses one
 * would be answering a question nobody asked. Nothing is fetched until a name
 * is submitted, so the repo page costs the same as before for readers who never
 * use it.
 */

import { useMutation } from "@tanstack/react-query";
import { ArrowRight, CornerDownRight, GitBranch, Loader2 } from "lucide-react";
import { useState } from "react";

import { ApiError, getTrace, type TraceOut } from "@/lib/api";
import { cn } from "@/lib/utils";

const DIRECTIONS = [
  { key: "out", label: "reaches", hint: "what this symbol calls" },
  { key: "in", label: "reached by", hint: "what calls this symbol" },
] as const;

export function TracePanel({ repoId }: { repoId: string }) {
  const [symbol, setSymbol] = useState("");
  const [direction, setDirection] = useState<"in" | "out">("out");

  // A mutation, not a query: this runs when asked, and the "no symbol yet"
  // state is `idle` rather than a query disabled by a falsy key.
  const trace = useMutation<TraceOut, Error, void>({
    mutationFn: () => getTrace(repoId, symbol.trim(), { direction }),
  });

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (symbol.trim()) trace.mutate();
  };

  const notFound = trace.error instanceof ApiError && trace.error.status === 404;

  return (
    <section className="space-y-4 rounded-lg border bg-card p-4 sm:p-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="eyebrow">Trace</p>
          <h2 className="display mt-2 text-lg font-semibold sm:text-xl">
            Follow one symbol through the graph
          </h2>
          <p className="mt-1 text-xs text-muted-foreground">
            A bounded walk over resolved edges — no model call. A class is
            traced through its methods.
          </p>
        </div>
        <GitBranch className="mt-1 hidden size-5 shrink-0 text-primary sm:block" />
      </div>

      <form onSubmit={submit} className="flex flex-wrap items-center gap-2">
        <input
          value={symbol}
          onChange={(e) => setSymbol(e.target.value)}
          placeholder="Client, or pkg.module.Client"
          aria-label="Symbol to trace"
          className="min-w-0 flex-1 rounded-md border bg-background px-2.5 py-1.5 font-mono text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
        <div
          role="tablist"
          aria-label="Direction"
          className="flex items-center gap-0.5 rounded-md border p-0.5"
        >
          {DIRECTIONS.map((d) => (
            <button
              key={d.key}
              type="button"
              role="tab"
              aria-selected={direction === d.key}
              title={d.hint}
              onClick={() => setDirection(d.key)}
              className={cn(
                "rounded-sm px-2.5 py-1 text-xs font-medium transition-colors",
                direction === d.key
                  ? "bg-secondary text-secondary-foreground"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {d.label}
            </button>
          ))}
        </div>
        <button
          type="submit"
          disabled={!symbol.trim() || trace.isPending}
          className="inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-xs font-medium transition-colors hover:border-primary/40 disabled:opacity-50"
        >
          {trace.isPending ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <ArrowRight className="size-3.5" />
          )}
          Trace
        </button>
      </form>

      {notFound && (
        <p className="text-xs text-muted-foreground">
          No symbol named <span className="font-mono">{symbol}</span> in this
          repo&rsquo;s index.
        </p>
      )}
      {trace.isError && !notFound && (
        <p className="text-xs text-muted-foreground">
          Could not run the trace.
        </p>
      )}

      {trace.data && (
        <div className="space-y-2">
          <p className="font-mono text-[11px] text-muted-foreground">
            {trace.data.root.qualname}
            <span className="mx-1.5">
              {direction === "out" ? "reaches" : "is reached by"}
            </span>
            {trace.data.nodes.length}
            {trace.data.truncated && "+"} symbol
            {trace.data.nodes.length === 1 ? "" : "s"}
          </p>

          {trace.data.nodes.length === 0 ? (
            <p className="text-xs text-muted-foreground">
              Nothing resolved in that direction — which for a leaf, or a symbol
              only reached through a re-export, is the true answer.
            </p>
          ) : (
            <ul className="max-h-72 space-y-1 overflow-y-auto">
              {trace.data.nodes.map((n) => (
                <li
                  key={n.qualname}
                  className="flex items-baseline gap-1.5 text-[11px]"
                  // Indent by hop, so the shape of the walk is visible without
                  // rebuilding the tree — depth is the only nesting that matters.
                  style={{ paddingLeft: `${(n.depth - 1) * 14}px` }}
                >
                  <CornerDownRight className="size-3 shrink-0 text-muted-foreground/50" />
                  <span className="font-mono">{n.qualname}</span>
                  <span className="text-muted-foreground/60">{n.kind}</span>
                  <span className="ml-auto shrink-0 font-mono text-muted-foreground/60">
                    {n.file_path}:{n.start_line}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {trace.data.truncated && (
            <p className="text-[11px] text-muted-foreground/70">
              Stopped at the node cap — the nearest hops are kept.
            </p>
          )}
        </div>
      )}
    </section>
  );
}
