"use client";

/**
 * What this repo stands on (§26.2, FEATURE-IDEAS 2.5).
 *
 * The symbol graph describes what the repo *contains*. This is the other half
 * of understanding an unfamiliar codebase: what it leans on from outside, and
 * where. `§6.1` deliberately refuses to answer that — an import resolved into
 * site-packages is dropped, because the graph is about this repository — so
 * these rows come from the import statements themselves, read out of the AST.
 *
 * The two disagreement lists are the reason this is not just a package count.
 * "Imported but never declared" is a fresh clone that fails; "declared but
 * never imported" is weight nobody is carrying on purpose. Neither is visible
 * from a manifest or from the code alone.
 *
 * Costs no model call: the whole panel is three SQL reads.
 */

import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Box, PackageX } from "lucide-react";
import { useState } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { getDependencies, getDependencyUses } from "@/lib/api";
import { ApiError } from "@/lib/api-client";
import type { DependencyOut } from "@/lib/api-types";
import { cn } from "@/lib/utils";

/** Packages listed before "show all". Enough to see what dominates. */
const PREVIEW = 10;

function UseList({
  repoId,
  module,
  includeTests,
}: {
  repoId: string;
  module: string;
  includeTests: boolean;
}) {
  const uses = useQuery({
    queryKey: ["dependency-uses", repoId, module, includeTests],
    queryFn: () => getDependencyUses(repoId, module, includeTests),
    staleTime: Infinity,
  });

  if (uses.isPending) return <Skeleton className="h-4 w-48" />;
  if (uses.isError || uses.data.uses.length === 0) {
    return (
      <p className="text-xs text-muted-foreground">No import sites to show.</p>
    );
  }
  return (
    // Scrolls in its own box rather than growing the page: werkzeug has 59
    // import sites in flask, and expanded inline they push everything below
    // the panel off the screen — the list stops being a detail and becomes
    // the page.
    <ul className="max-h-64 space-y-1 overflow-y-auto rounded-md border bg-background/50 p-2">
      {uses.data.uses.map((u) => (
        <li
          key={`${u.file_path}:${u.start_line}`}
          className="flex items-baseline gap-2 font-mono text-[11px]"
        >
          <span className="truncate text-muted-foreground">
            {u.file_path}:{u.start_line}
          </span>
          <span className="truncate text-foreground">{u.dotted}</span>
        </li>
      ))}
      {uses.data.truncated && (
        <li className="text-[11px] text-muted-foreground">
          Showing the first {uses.data.uses.length} sites.
        </li>
      )}
    </ul>
  );
}

function PackageRow({
  pkg,
  repoId,
  includeTests,
  max,
}: {
  pkg: DependencyOut;
  repoId: string;
  includeTests: boolean;
  max: number;
}) {
  const [open, setOpen] = useState(false);
  const share = max > 0 ? Math.max(0.04, pkg.n_uses / max) : 0;

  return (
    <li>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="group flex w-full items-center gap-3 py-2 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <span className="w-40 shrink-0 truncate font-mono text-xs font-medium">
          {pkg.module}
        </span>
        <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-secondary">
          <span
            className="block h-full rounded-full bg-primary/70"
            style={{ width: `${share * 100}%` }}
          />
        </span>
        <span className="shrink-0 font-mono text-[11px] text-muted-foreground">
          {pkg.n_uses} import{pkg.n_uses === 1 ? "" : "s"}
        </span>
        {!pkg.declared && (
          <span
            className="shrink-0 rounded-sm bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-medium text-amber-700 dark:text-amber-400"
            title="No manifest row under this name"
          >
            undeclared
          </span>
        )}
      </button>
      {open && (
        <div className="space-y-2 pb-3 pl-3">
          {pkg.requirement && (
            <p className="font-mono text-[11px] text-muted-foreground">
              {pkg.requirement}
              {pkg.sources.length > 0 && ` · ${pkg.sources.join(", ")}`}
              {pkg.extras.length > 0 && ` · [${pkg.extras.join(", ")}]`}
            </p>
          )}
          <UseList repoId={repoId} module={pkg.module} includeTests={includeTests} />
        </div>
      )}
    </li>
  );
}

export function DependenciesPanel({ repoId }: { repoId: string }) {
  const [includeTests, setIncludeTests] = useState(false);
  const [showAll, setShowAll] = useState(false);

  const deps = useQuery({
    queryKey: ["dependencies", repoId, includeTests],
    queryFn: () => getDependencies(repoId, includeTests),
    // Immutable snapshot (§14.3) — once fetched it cannot go stale.
    staleTime: Infinity,
    retry: (count, err) =>
      !(err instanceof ApiError && err.status === 404) && count < 1,
  });

  if (deps.isPending) {
    return (
      <section className="space-y-3 rounded-lg border bg-card p-4 sm:p-5">
        <Skeleton className="h-4 w-40" />
        {[...Array(3)].map((_, i) => (
          <Skeleton key={i} className="h-7 w-full" />
        ))}
      </section>
    );
  }
  // An extra must not take the page down with it.
  if (deps.isError) return null;

  const { indexed, packages, undeclared, unused, truncated } = deps.data;

  // §26.3. A snapshot ingested before the pass has no rows, and rendering an
  // empty panel would say "this project has no dependencies" — which for any
  // real repo is a confident lie. Say nothing instead.
  if (!indexed) return null;
  if (packages.length === 0 && unused.length === 0) return null;

  const max = Math.max(...packages.map((p) => p.n_uses), 0);
  const shown = showAll ? packages : packages.slice(0, PREVIEW);

  return (
    <section className="space-y-4 rounded-lg border bg-card p-4 sm:p-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="eyebrow">Dependencies</p>
          <h2 className="display mt-2 text-lg font-semibold sm:text-xl">
            What this codebase stands on.
          </h2>
          <p className="mt-1.5 max-w-md text-[13px] leading-relaxed text-muted-foreground">
            Third-party packages ranked by how often they are imported, read
            from the import statements rather than resolved — so the answer does
            not depend on what happens to be installed.
          </p>
        </div>
        <Box className="mt-1 hidden size-5 shrink-0 text-primary sm:block" />
      </div>

      <label className="flex w-fit cursor-pointer items-center gap-2 text-xs text-muted-foreground">
        <input
          type="checkbox"
          checked={includeTests}
          onChange={(e) => setIncludeTests(e.target.checked)}
          className="size-3.5 accent-[hsl(var(--primary))]"
        />
        Include test files
      </label>

      {packages.length > 0 && (
        <ol className="divide-y border-y">
          {shown.map((p) => (
            <PackageRow
              key={p.module}
              pkg={p}
              repoId={repoId}
              includeTests={includeTests}
              max={max}
            />
          ))}
        </ol>
      )}

      {packages.length > PREVIEW && (
        <button
          type="button"
          onClick={() => setShowAll((v) => !v)}
          className="rounded-sm text-xs font-medium text-primary transition-opacity hover:opacity-80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {showAll ? "Show fewer" : `Show all ${packages.length} packages`}
        </button>
      )}

      {/* The two disagreements. Rendered as warnings rather than facts: the
          declared↔imported match is by normalised name, and a distribution
          need not ship a module of the same name (§26.2). */}
      {(undeclared.length > 0 || unused.length > 0) && (
        <div className="grid gap-3 sm:grid-cols-2">
          {undeclared.length > 0 && (
            <div className="space-y-1.5 rounded-md border p-3">
              <p className="flex items-center gap-1.5 text-xs font-medium">
                <AlertTriangle className="size-3.5 text-amber-500" />
                Imported, not declared
              </p>
              <p className="font-mono text-[11px] leading-relaxed text-muted-foreground">
                {undeclared.join(", ")}
              </p>
              <p className="text-[11px] leading-relaxed text-muted-foreground">
                Works here, may fail on a fresh clone — unless the package ships
                a module under a different name.
              </p>
            </div>
          )}
          {unused.length > 0 && (
            <div className="space-y-1.5 rounded-md border p-3">
              <p className="flex items-center gap-1.5 text-xs font-medium">
                <PackageX className="size-3.5 text-muted-foreground" />
                Declared, never imported
              </p>
              <p className="font-mono text-[11px] leading-relaxed text-muted-foreground">
                {unused.map((u) => u.name).join(", ")}
              </p>
              <p className="text-[11px] leading-relaxed text-muted-foreground">
                Counting test imports as usage, so this is not just a test
                dependency.
              </p>
            </div>
          )}
        </div>
      )}

      {truncated && (
        <p className={cn("text-xs text-muted-foreground")}>
          Top {packages.length} packages shown.
        </p>
      )}
    </section>
  );
}
