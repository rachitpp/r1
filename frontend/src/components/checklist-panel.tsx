"use client";

/**
 * The onboarding checklist (SPEC §22, FEATURE-IDEAS 6.5).
 *
 * Five numbered steps, in reading order, each one a real range and a question.
 * The overview above it *describes* the repo; this one hands the reader
 * something to do next — which is the difference between a summary and an
 * onboarding path.
 *
 * Every step is a link into chat with `?q=` pre-filled, the affordance 3.5
 * built and 3.1 reused. Nothing here costs a model call: the whole list is a
 * read over the symbol graph (§22.1), so it renders instantly and identically
 * every time.
 */

import { useQuery } from "@tanstack/react-query";
import { ArrowRight, ListChecks } from "lucide-react";
import Link from "next/link";

import { Skeleton } from "@/components/ui/skeleton";
import { getChecklist } from "@/lib/api";

export function ChecklistPanel({ repoId }: { repoId: string }) {
  const checklist = useQuery({
    queryKey: ["checklist", repoId],
    queryFn: () => getChecklist(repoId),
    // Deterministic over an immutable snapshot (§14.3).
    staleTime: Infinity,
    retry: false,
  });

  if (checklist.isPending) {
    return (
      <section className="space-y-3 rounded-lg border bg-card p-4 sm:p-5">
        <Skeleton className="h-4 w-44" />
        <Skeleton className="h-3 w-full" />
        <Skeleton className="h-3 w-5/6" />
      </section>
    );
  }

  // A repo whose graph yields nothing has nothing to suggest. Rendering an
  // empty numbered list would be worse than rendering nothing at all.
  if (checklist.isError || !checklist.data?.items.length) return null;

  const { items } = checklist.data;

  return (
    <section className="space-y-4 rounded-lg border bg-card p-4 sm:p-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="eyebrow">Start here</p>
          <h2 className="display mt-2 text-lg font-semibold sm:text-xl">
            The first {items.length} thing{items.length === 1 ? "" : "s"} to
            understand
          </h2>
          <p className="mt-1 text-xs text-muted-foreground">
            Derived from the symbol graph — no model involved, so it is the same
            list every time.
          </p>
        </div>
        <ListChecks className="mt-1 hidden size-5 shrink-0 text-primary sm:block" />
      </div>

      <ol className="space-y-3">
        {items.map((item, i) => (
          <li key={item.kind} className="flex gap-3">
            <span
              aria-hidden
              className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full bg-secondary font-mono text-[10px] font-medium text-secondary-foreground"
            >
              {i + 1}
            </span>
            <div className="min-w-0 flex-1">
              <h3 className="text-sm font-medium leading-snug">{item.title}</h3>
              <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">
                {item.detail}
              </p>
              <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1">
                <span className="font-mono text-[11px] text-muted-foreground/70">
                  {item.file_path}:{item.start_line}-{item.end_line}
                </span>
                <Link
                  href={`/repos/${repoId}/chat?q=${encodeURIComponent(item.question)}`}
                  className="group inline-flex items-center gap-1 rounded-sm text-[11px] font-medium text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  Ask this
                  <ArrowRight className="size-3 transition-transform group-hover:translate-x-0.5" />
                </Link>
              </div>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}
