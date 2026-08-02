"use client";

/**
 * The §18.2 module rollup, on `/repos/[id]`.
 *
 * This is the symbol graph answering a *global* question — "what are the main
 * modules and how do they depend on each other" — which the agent is bad at and
 * which nothing in the product asked before. It costs no model call and no tool
 * budget: the ranking is a `GROUP BY`, so it is the same every time.
 *
 * Ranked by fan-in, because "how much of this repo leans on you" is the closest
 * thing the graph has to importance. The bar is that number relative to the
 * top module, so the shape of the codebase reads at a glance — one hub with a
 * long tail looks different from a flat mesh, and that difference is the point.
 *
 * Each module expands into both directions and offers a pre-filled question,
 * which is what turns a map into a starting point rather than a diagram.
 */

import { useQuery } from "@tanstack/react-query";
import { ArrowRight, ChevronRight, Network } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { ArchitectureDiagram } from "@/components/architecture-diagram";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError, getArchitecture, type ModuleNode } from "@/lib/api";
import {
  type ModuleLink,
  dependenciesOf,
  dependentsOf,
  linkLabel,
  moduleQuestion,
  shareOf,
  splitModulePath,
} from "@/lib/architecture";
import { cn } from "@/lib/utils";

/** Modules shown before "show all". Enough to see the shape, short enough to scan. */
const PREVIEW = 8;

function ModulePath({ path }: { path: string }) {
  const { dir, file } = splitModulePath(path);
  return (
    <span className="truncate font-mono text-xs">
      {dir && <span className="text-muted-foreground">{dir}</span>}
      <span className="font-medium text-foreground">{file}</span>
    </span>
  );
}

/** One side of a module's coupling — "Depends on" or "Used by". */
function LinkList({
  title,
  links,
  onPick,
}: {
  title: string;
  links: ModuleLink[];
  onPick: (path: string) => void;
}) {
  if (links.length === 0) {
    return (
      <div>
        <p className="font-mono text-[10px] uppercase tracking-[0.1em] text-muted-foreground">
          {title}
        </p>
        <p className="mt-1.5 text-xs text-muted-foreground/70">Nothing.</p>
      </div>
    );
  }
  return (
    <div className="min-w-0">
      <p className="font-mono text-[10px] uppercase tracking-[0.1em] text-muted-foreground">
        {title}
      </p>
      <ul className="mt-1.5 space-y-1">
        {links.map((l) => (
          <li key={l.path} className="flex items-baseline gap-2">
            <button
              type="button"
              onClick={() => onPick(l.path)}
              className="min-w-0 truncate rounded-sm text-left transition-colors hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <ModulePath path={l.path} />
            </button>
            <span className="ml-auto shrink-0 font-mono text-[10px] tabular-nums text-muted-foreground">
              {linkLabel(l)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function ArchitecturePanel({ repoId }: { repoId: string }) {
  const [includeTests, setIncludeTests] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);
  // List first, deliberately: it needs no extra bundle and answers the common
  // question. The diagram is the second look, for shape rather than detail.
  const [view, setView] = useState<"list" | "diagram">("list");

  const arch = useQuery({
    queryKey: ["architecture", repoId, includeTests],
    queryFn: () => getArchitecture(repoId, includeTests),
    // Deterministic over an immutable snapshot (§14.3): once fetched for a repo
    // id it cannot go stale, so never refetch it.
    staleTime: Infinity,
    retry: (count, err) =>
      !(err instanceof ApiError && err.status === 404) && count < 1,
  });

  if (arch.isPending) {
    return (
      <section className="space-y-3 rounded-lg border bg-card p-4 sm:p-5">
        <Skeleton className="h-4 w-40" />
        {[...Array(4)].map((_, i) => (
          <Skeleton key={i} className="h-7 w-full" />
        ))}
      </section>
    );
  }
  // A failed rollup must not take the page down with it — the chat CTA above is
  // the thing that matters, and this is an extra.
  if (arch.isError) return null;

  const { nodes, edges, truncated } = arch.data;
  if (nodes.length === 0) return null;

  const maxFanIn = Math.max(...nodes.map((n) => n.fan_in), 0);
  const shown = showAll ? nodes : nodes.slice(0, PREVIEW);
  const linked = edges.length > 0;

  const toggle = (n: ModuleNode) =>
    setOpen((cur) => (cur === n.path ? null : n.path));

  return (
    <section className="space-y-4 rounded-lg border bg-card p-4 sm:p-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="eyebrow">Architecture</p>
          <h2 className="display mt-2 text-lg font-semibold sm:text-xl">
            How this codebase fits together.
          </h2>
          <p className="mt-1.5 max-w-md text-[13px] leading-relaxed text-muted-foreground">
            Modules ranked by how much of the repo depends on them, rolled up
            from the import and call graph. Computed by query, not by the model
            — the same answer every time.
          </p>
        </div>
        <Network className="mt-1 hidden size-5 shrink-0 text-primary sm:block" />
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <label className="flex w-fit cursor-pointer items-center gap-2 text-xs text-muted-foreground">
          <input
            type="checkbox"
            checked={includeTests}
            onChange={(e) => {
              setIncludeTests(e.target.checked);
              setOpen(null);
            }}
            className="size-3.5 accent-[hsl(var(--primary))]"
          />
          Include test files
        </label>

        {/* Only offered when there is a graph to draw: with no cross-module
            edges the picture is a row of disconnected boxes, which is worse
            than the list at saying the same thing. */}
        {linked && (
          <div
            role="tablist"
            aria-label="Architecture view"
            className="flex items-center gap-0.5 rounded-md border p-0.5"
          >
            {(["list", "diagram"] as const).map((v) => (
              <button
                key={v}
                type="button"
                role="tab"
                aria-selected={view === v}
                onClick={() => setView(v)}
                className={cn(
                  "rounded-sm px-2.5 py-1 text-xs font-medium capitalize transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  view === v
                    ? "bg-secondary text-secondary-foreground"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {v}
              </button>
            ))}
          </div>
        )}
      </div>

      {view === "diagram" && linked && (
        <ArchitectureDiagram
          nodes={nodes}
          edges={edges}
          onPick={(path) => {
            setOpen(path);
            setView("list");
            setShowAll(true);
          }}
        />
      )}

      <ol className={cn("divide-y border-y", view === "diagram" && "hidden")}>
        {shown.map((n) => {
          const isOpen = open === n.path;
          return (
            <li key={n.path}>
              <button
                type="button"
                onClick={() => toggle(n)}
                aria-expanded={isOpen}
                className="group flex w-full items-center gap-3 py-2 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <ChevronRight
                  className={cn(
                    "size-3.5 shrink-0 text-muted-foreground transition-transform",
                    isOpen && "rotate-90",
                  )}
                />
                <span className="min-w-0 flex-1 truncate">
                  <ModulePath path={n.path} />
                </span>
                {/* Fan-in as a bar relative to the top module. A bare number
                    tells you nothing without the distribution to compare it
                    against; the bar *is* the distribution. */}
                <span
                  aria-hidden
                  className="hidden h-1 w-20 shrink-0 overflow-hidden rounded-full bg-border sm:block"
                >
                  <span
                    className="block h-full rounded-full bg-primary/70"
                    style={{ width: `${shareOf(n.fan_in, maxFanIn) * 100}%` }}
                  />
                </span>
                <span className="shrink-0 font-mono text-[10px] tabular-nums text-muted-foreground">
                  <span
                    className="text-foreground/80"
                    title="Edges arriving from other modules"
                  >
                    ←{n.fan_in}
                  </span>{" "}
                  <span title="Edges leaving for other modules">→{n.fan_out}</span>
                </span>
              </button>

              {isOpen && (
                <div className="space-y-4 pb-4 pl-6 pr-1">
                  <div className="grid gap-4 sm:grid-cols-2">
                    <LinkList
                      title="Depends on"
                      links={dependenciesOf(edges, n.path)}
                      onPick={setOpen}
                    />
                    <LinkList
                      title="Used by"
                      links={dependentsOf(edges, n.path)}
                      onPick={setOpen}
                    />
                  </div>
                  <p className="font-mono text-[10px] text-muted-foreground">
                    {n.n_symbols} symbol{n.n_symbols === 1 ? "" : "s"} defined
                  </p>
                  {/* The map's payoff: a module you now have a question about,
                      one click from an answer. `?q=` asks it on arrival. */}
                  <Link
                    href={`/repos/${repoId}/chat?q=${encodeURIComponent(moduleQuestion(n.path))}`}
                    className="group inline-flex items-center gap-1.5 rounded-sm text-xs font-medium text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    Ask what this module does
                    <ArrowRight className="size-3 transition-transform group-hover:translate-x-0.5" />
                  </Link>
                </div>
              )}
            </li>
          );
        })}
      </ol>

      <div
        className={cn(
          "flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground",
          view === "diagram" && "hidden",
        )}
      >
        {nodes.length > PREVIEW ? (
          <button
            type="button"
            onClick={() => setShowAll((v) => !v)}
            className="rounded-sm font-medium text-primary transition-opacity hover:opacity-80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {showAll ? "Show fewer" : `Show all ${nodes.length} modules`}
          </button>
        ) : (
          <span />
        )}
        {truncated && <span>Top {nodes.length} modules shown.</span>}
      </div>

      {/* Not an error state. Jedi drops everything outside the repo (§6.1), and
          resolution varies a lot by repo — 4% unresolved on httpx, 15% on
          flask's `src/` — so a thin repo legitimately has no cross-module edges
          at all. Say so rather than showing empty lists. */}
      {!linked && (
        <p className="text-xs text-muted-foreground">
          No cross-module edges resolved for this repo — every module here
          stands alone.
        </p>
      )}
    </section>
  );
}
