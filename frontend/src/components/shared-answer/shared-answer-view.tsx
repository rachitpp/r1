"use client";

/**
 * Renders one published answer for a reader who may have no account (§21.3).
 *
 * Deliberately a *document*, not the app shell: no split pane, no code viewer,
 * no "New chat". The reader followed a link to see one answer, and every
 * affordance that would 401 them is absent rather than present-and-broken.
 *
 * Citations become **GitHub blob links at the pinned commit**, reusing 6.2's
 * `githubBlobUrl`. That is the same choice the Markdown export made and for the
 * same reason: a bare `_client.py:718-738` means nothing to someone without
 * this page open, and the link is only stable because a snapshot is frozen
 * (§14.3).
 */

import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, ArrowRight, CircleHelp, ExternalLink } from "lucide-react";
import Link from "next/link";

import { AnswerBody } from "@/components/answer/answer-body";
import { Skeleton } from "@/components/ui/skeleton";
import { getSharedAnswer } from "@/lib/api";
import { ApiError } from "@/lib/api-client";
import { citationKey } from "@/lib/citations";
import { githubBlobUrl, shortSha } from "@/lib/format";
import { parseMarkdown } from "@/lib/markdown";
import { parseUncertainty } from "@/lib/uncertainty";

export function SharedAnswerView({ shareId }: { shareId: string }) {
  const shared = useQuery({
    queryKey: ["shared", shareId],
    // A published answer never changes — the row is written once and the
    // snapshot behind it is frozen.
    staleTime: Infinity,
    retry: false,
    queryFn: () => getSharedAnswer(shareId),
  });

  if (shared.isPending) {
    return (
      <main className="mx-auto w-full max-w-3xl px-4 py-12">
        <Skeleton className="h-4 w-40" />
        <Skeleton className="mt-6 h-8 w-3/4" />
        <div className="mt-8 space-y-3">
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-2/3" />
        </div>
      </main>
    );
  }

  if (shared.isError) {
    const gone =
      shared.error instanceof ApiError && shared.error.status === 404;
    return (
      <main className="mx-auto w-full max-w-3xl px-4 py-20 text-center">
        <AlertTriangle className="mx-auto size-8 text-muted-foreground" />
        <h1 className="display mt-4 text-xl font-semibold">
          {gone ? "This link is no longer available" : "Could not load this answer"}
        </h1>
        <p className="mx-auto mt-2 max-w-md text-sm text-muted-foreground">
          {gone
            ? "The answer was either never here or has been unpublished by whoever shared it."
            : "The API did not respond. If you are running this locally, check the backend is up."}
        </p>
        <Link
          href="/"
          className="mt-6 inline-flex items-center gap-1.5 text-sm text-primary hover:underline"
        >
          Index a repository <ArrowRight className="size-3.5" />
        </Link>
      </main>
    );
  }

  const a = shared.data;
  // A shared answer carries §25's marker too — a reader who followed a link
  // deserves the same caveat the asker saw, not a stray bracket.
  const { body, uncertainty } = parseUncertainty(a.answer);
  const blocks = parseMarkdown(body);

  return (
    <main className="mx-auto w-full max-w-3xl px-4 py-10 sm:py-14">
      <header className="space-y-3">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
          <a
            href={a.repo_url}
            target="_blank"
            rel="noreferrer noopener"
            className="inline-flex items-center gap-1 font-mono hover:text-foreground"
          >
            {a.repo_name}
            <ExternalLink className="size-3" />
          </a>
          {a.commit_sha && (
            <>
              <span aria-hidden>·</span>
              <span className="font-mono">
                indexed at {shortSha(a.commit_sha)}
              </span>
            </>
          )}
          {a.model && (
            <>
              <span aria-hidden>·</span>
              {/* Named for the same reason §19 names it: this is model-written
                  prose, and a reader comparing two answers deserves to know
                  whether the same thing wrote them. */}
              <span>answered by {a.model}</span>
            </>
          )}
        </div>

        <div className="flex gap-3">
          <span aria-hidden className="w-0.5 shrink-0 self-stretch bg-primary" />
          <h1 className="display min-w-0 text-2xl font-semibold leading-snug">
            {a.question}
          </h1>
        </div>
      </header>

      <div className="mt-8">
        <AnswerBody blocks={blocks} onCiteClick={() => {}} activeKey={null} />
      </div>

      {uncertainty && (
        <div className="mt-5 flex items-start gap-2 rounded-md border border-dashed bg-secondary/30 px-3 py-2 text-xs text-muted-foreground">
          <CircleHelp className="mt-0.5 size-3.5 shrink-0" />
          <p className="leading-relaxed">
            <span className="font-medium text-foreground">
              Not fully confirmed:
            </span>{" "}
            {uncertainty}
          </p>
        </div>
      )}

      {a.citations.length > 0 && (
        <section className="mt-10 border-t pt-6">
          <h2 className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
            Sources
          </h2>
          <ul className="mt-3 space-y-1.5">
            {a.citations.map((c) => {
              const href = githubBlobUrl(a.repo_url, a.commit_sha, c);
              const label = citationKey(c);
              return (
                <li key={label}>
                  {href ? (
                    <a
                      href={href}
                      target="_blank"
                      rel="noreferrer noopener"
                      className="inline-flex items-center gap-1.5 font-mono text-xs text-muted-foreground transition-colors hover:text-primary"
                    >
                      {label}
                      <ExternalLink className="size-3 shrink-0" />
                    </a>
                  ) : (
                    <span className="font-mono text-xs text-muted-foreground">
                      {label}
                    </span>
                  )}
                </li>
              );
            })}
          </ul>
        </section>
      )}

      <footer className="mt-12 border-t pt-6 text-xs text-muted-foreground">
        Answered from the repository&rsquo;s source at a pinned commit, with
        citations.{" "}
        <Link href="/" className="text-primary hover:underline">
          Index a repository of your own
        </Link>
        .
      </footer>
    </main>
  );
}
