"use client";

/**
 * The §19 "start here" guide, at the top of `/repos/[id]`.
 *
 * This is the answer to "what do I even ask?" — the question that makes a
 * blank chat box the wrong first surface for a repo nobody knows. It is written
 * once per snapshot and stored, so the cost is one model call ever, and the
 * page it lands on is instant from then on.
 *
 * Citations render as the same chips a chat answer uses and open the same
 * viewer, except there is no viewer on this page — so a chip navigates to the
 * chat route with the file pre-selected instead. One concept, two surfaces.
 *
 * Each `##` section gets an "ask more" link built from its own heading, which
 * is what turns a document into a set of doors. No extra model output is
 * needed for that: the headings are already there.
 */

import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Compass, RotateCcw } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { AnswerBody } from "@/components/answer/answer-body";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { getOverview } from "@/lib/api";
import { ApiError } from "@/lib/api-client";
import type { Citation } from "@/lib/citations";
import { type Block, parseMarkdown } from "@/lib/markdown";
import { sectionQuestion, splitSections } from "@/lib/overview";

/** While generating. Slower than a normal poll — this waits on a model, not a query. */
const POLL_MS = 2500;

function Frame({ children }: { children: React.ReactNode }) {
  return (
    <section className="space-y-4 rounded-lg border bg-card p-4 sm:p-5">
      <div>
        <p className="eyebrow">Start here</p>
        {children}
      </div>
    </section>
  );
}

export function OverviewPanel({ repoId }: { repoId: string }) {
  const router = useRouter();
  const [retrying, setRetrying] = useState(false);

  const overview = useQuery({
    queryKey: ["overview", repoId],
    queryFn: () => getOverview(repoId, retrying),
    // Poll only while the worker is writing it. A `ready` overview describes an
    // immutable snapshot (§14.3) and can never change, so polling past that
    // would be asking a question with a known answer forever.
    refetchInterval: (q) =>
      q.state.data?.status === "generating" ? POLL_MS : false,
    retry: (count, err) =>
      !(err instanceof ApiError && err.status === 404) && count < 1,
  });

  // No viewer on this page, so a citation opens the chat with the file already
  // in hand rather than doing nothing.
  const openCitation = (c: Citation) => {
    router.push(
      `/repos/${repoId}/chat?q=${encodeURIComponent(
        `Walk me through [${c.file_path}:${c.start_line}-${c.end_line}].`,
      )}`,
    );
  };

  if (overview.isPending) {
    return (
      <Frame>
        <Skeleton className="mt-2 h-6 w-56" />
        <div className="mt-4 space-y-2">
          {[...Array(4)].map((_, i) => (
            <Skeleton key={i} className="h-4 w-full" />
          ))}
        </div>
      </Frame>
    );
  }
  // An overview is an extra. If it cannot load, the page it sits on — stats,
  // chat CTA, architecture — is still the page.
  if (overview.isError) return null;

  const data = overview.data;

  if (data.status === "generating") {
    return (
      <Frame>
        <h2 className="display mt-2 flex items-center gap-2 text-lg font-semibold sm:text-xl">
          <Compass className="size-4 animate-pulse text-primary" />
          Reading the codebase…
        </h2>
        <p className="mt-1.5 max-w-md text-[13px] leading-relaxed text-muted-foreground">
          Writing a short guide to this repository from its symbol graph. This
          happens once — the result is stored against this commit.
        </p>
        <div className="mt-4 space-y-2" aria-hidden>
          {[...Array(3)].map((_, i) => (
            <Skeleton key={i} className="h-4" style={{ width: `${92 - i * 14}%` }} />
          ))}
        </div>
      </Frame>
    );
  }

  if (data.status === "failed") {
    return (
      <Frame>
        <h2 className="display mt-2 text-lg font-semibold sm:text-xl">
          Couldn&apos;t write the overview.
        </h2>
        <p className="mt-1.5 text-[13px] text-muted-foreground">
          {data.error ?? "The generation failed."}
        </p>
        <Button
          variant="outline"
          size="sm"
          className="mt-3"
          onClick={() => {
            setRetrying(true);
            void overview.refetch().finally(() => setRetrying(false));
          }}
        >
          <RotateCcw className="size-3.5" />
          Try again
        </Button>
      </Frame>
    );
  }

  const blocks: Block[] = parseMarkdown(data.body ?? "");
  const sections = splitSections(blocks);
  if (sections.length === 0) return null;

  return (
    <section className="space-y-5 rounded-lg border bg-card p-4 sm:p-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="eyebrow">Start here</p>
          <h2 className="display mt-2 text-lg font-semibold sm:text-xl">
            A guide to this repository.
          </h2>
        </div>
        <Compass className="mt-1 hidden size-5 shrink-0 text-primary sm:block" />
      </div>

      {sections.map((section, i) => (
        <div key={i} className="border-t pt-4 first:border-t-0 first:pt-0">
          {section.title && (
            <h3 className="display text-[15px] font-semibold">{section.title}</h3>
          )}
          <div className="mt-2">
            <AnswerBody
              blocks={section.blocks}
              onCiteClick={openCitation}
              activeKey={null}
            />
          </div>
          {section.title && (
            <button
              type="button"
              onClick={() =>
                router.push(
                  `/repos/${repoId}/chat?q=${encodeURIComponent(
                    sectionQuestion(section.title!),
                  )}`,
                )
              }
              className="group mt-2 inline-flex items-center gap-1.5 rounded-sm text-xs font-medium text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              Ask more about this
              <ArrowRight className="size-3 transition-transform group-hover:translate-x-0.5" />
            </button>
          )}
        </div>
      ))}

      {/* Named, not hidden: this section is model-written prose about code,
          which is a different kind of claim from the counts above it. */}
      <p className="border-t pt-3 text-[11px] text-muted-foreground">
        Written from the symbol graph by{" "}
        <span className="font-mono">{data.model ?? "the configured model"}</span>
        , once, for this commit. Every claim links to the code it came from.
      </p>
    </section>
  );
}
