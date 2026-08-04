"use client";

/**
 * What changed between two snapshots of this repo (§28, FEATURE-IDEAS 6.3).
 *
 * A *structural* diff, not a textual one. `git diff` answers "which lines
 * changed" and answers it better; what only this can answer is what the index
 * now holds — which files, symbols and third-party packages exist that did not
 * before, plus the commits between.
 *
 * **This panel is also where a second snapshot comes from.** Until now nothing
 * in the UI could create one: every ingest took the branch tip, so a repo only
 * ever had one commit indexed and there was never a pair to compare. The commit
 * field below posts `rev` (§28.3), which is the whole reason the feature is
 * reachable from a browser at all.
 *
 * Costs no model call: four SQL reads over two immutable corpora.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { GitCompare, Loader2 } from "lucide-react";
import { useState } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { createRepo, getComparison, getSiblingSnapshots } from "@/lib/api";
import { ApiError } from "@/lib/api-client";
import type { ChangedSymbol } from "@/lib/api-types";
import { cn } from "@/lib/utils";

const SHOWN = 8;

function short(sha: string | null): string {
  return sha ? sha.slice(0, 8) : "unknown";
}

function SymbolList({
  title,
  tone,
  symbols,
}: {
  title: string;
  tone: "added" | "removed";
  symbols: ChangedSymbol[];
}) {
  const [all, setAll] = useState(false);
  if (symbols.length === 0) return null;
  const shown = all ? symbols : symbols.slice(0, SHOWN);
  return (
    <div className="space-y-1.5">
      <p className="text-xs font-medium">
        {title}{" "}
        <span className="font-mono text-[11px] text-muted-foreground">
          {symbols.length}
        </span>
      </p>
      <ul className="space-y-0.5">
        {shown.map((s) => (
          <li key={`${s.qualname}:${s.file_path}`} className="flex gap-2">
            <span
              className={cn(
                "shrink-0 font-mono text-[11px]",
                tone === "added"
                  ? "text-emerald-600 dark:text-emerald-400"
                  : "text-red-600 dark:text-red-400",
              )}
            >
              {tone === "added" ? "+" : "−"}
            </span>
            <span className="truncate font-mono text-[11px]">
              <span className="text-muted-foreground">{s.kind} </span>
              {s.qualname}
            </span>
          </li>
        ))}
      </ul>
      {symbols.length > SHOWN && (
        <button
          type="button"
          onClick={() => setAll((v) => !v)}
          className="rounded-sm text-[11px] font-medium text-primary hover:opacity-80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {all ? "Show fewer" : `Show all ${symbols.length}`}
        </button>
      )}
    </div>
  );
}

export function ComparePanel({
  repoId,
  repoUrl,
}: {
  repoId: string;
  repoUrl: string;
}) {
  const queryClient = useQueryClient();
  const [base, setBase] = useState<string | null>(null);
  const [rev, setRev] = useState("");

  const siblings = useQuery({
    queryKey: ["siblings", repoId],
    queryFn: () => getSiblingSnapshots(repoId),
    retry: (count, err) =>
      !(err instanceof ApiError && err.status === 404) && count < 1,
  });

  const comparison = useQuery({
    queryKey: ["compare", repoId, base],
    queryFn: () => getComparison(repoId, base as string),
    // Both sides are immutable snapshots (§14.3), so a result cannot go stale.
    staleTime: Infinity,
    enabled: base !== null,
  });

  const index = useMutation({
    mutationFn: (commit: string) => createRepo(repoUrl, commit),
    onSuccess: () => {
      setRev("");
      void queryClient.invalidateQueries({ queryKey: ["siblings", repoId] });
    },
  });

  if (siblings.isPending) {
    return (
      <section className="space-y-3 rounded-lg border bg-card p-4 sm:p-5">
        <Skeleton className="h-4 w-40" />
        <Skeleton className="h-7 w-full" />
      </section>
    );
  }
  if (siblings.isError) return null;

  const options = siblings.data.siblings.filter((s) => s.status === "ready");
  const pending = siblings.data.siblings.filter((s) => s.status !== "ready");

  return (
    <section className="space-y-4 rounded-lg border bg-card p-4 sm:p-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="eyebrow">Compare</p>
          <h2 className="display mt-2 text-lg font-semibold sm:text-xl">
            What changed since an earlier commit.
          </h2>
          <p className="mt-1.5 max-w-md text-[13px] leading-relaxed text-muted-foreground">
            Files, symbols and packages that exist now and did not before —
            structural, not a line diff. Computed by query, so the answer is the
            same every time.
          </p>
        </div>
        <GitCompare className="mt-1 hidden size-5 shrink-0 text-primary sm:block" />
      </div>

      {/* Index another commit. Without this the panel has nothing to offer on a
          repo indexed once, which is every repo until someone asks for a
          second — the gap that kept §28 out of the UI entirely. */}
      <form
        className="flex flex-wrap items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (rev.trim()) index.mutate(rev.trim());
        }}
      >
        <input
          value={rev}
          onChange={(e) => setRev(e.target.value)}
          placeholder="commit sha, tag or branch"
          className="min-w-52 flex-1 rounded-md border bg-background px-2.5 py-1.5 font-mono text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
        <button
          type="submit"
          disabled={!rev.trim() || index.isPending}
          className="inline-flex items-center gap-1.5 rounded-md bg-secondary px-3 py-1.5 text-xs font-medium text-secondary-foreground disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {index.isPending && <Loader2 className="size-3 animate-spin" />}
          Index this commit
        </button>
      </form>
      {index.isError && (
        <p className="text-xs text-destructive">
          {index.error instanceof ApiError
            ? index.error.message
            : "Could not start that ingest."}
        </p>
      )}
      {pending.length > 0 && (
        <p className="text-xs text-muted-foreground">
          {pending.length} snapshot{pending.length === 1 ? "" : "s"} still
          indexing — they appear here once ready.
        </p>
      )}

      {options.length === 0 ? (
        <p className="text-xs leading-relaxed text-muted-foreground">
          Only one commit of this repo is indexed, so there is nothing to
          compare yet. Index an earlier one above and it will show up here.
        </p>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-muted-foreground">Compare against</span>
          {options.map((s) => (
            <button
              key={s.id}
              type="button"
              onClick={() => setBase(s.id)}
              className={cn(
                "rounded-md border px-2.5 py-1 font-mono text-[11px] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                base === s.id
                  ? "border-primary bg-primary/10 text-foreground"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {short(s.commit_sha)}
            </button>
          ))}
        </div>
      )}

      {comparison.isPending && base !== null && (
        <Skeleton className="h-24 w-full" />
      )}
      {comparison.isError && (
        <p className="text-xs text-destructive">
          {comparison.error instanceof ApiError
            ? comparison.error.message
            : "Could not compare those snapshots."}
        </p>
      )}

      {comparison.data && (
        <div className="space-y-4 border-t pt-4">
          <p className="font-mono text-[11px] text-muted-foreground">
            {short(comparison.data.base.commit_sha)} →{" "}
            {short(comparison.data.head.commit_sha)}
          </p>

          <div className="grid gap-4 sm:grid-cols-2">
            <SymbolList
              title="Symbols added"
              tone="added"
              symbols={comparison.data.symbols_added}
            />
            <SymbolList
              title="Symbols removed"
              tone="removed"
              symbols={comparison.data.symbols_removed}
            />
          </div>

          {(comparison.data.files_added.length > 0 ||
            comparison.data.files_removed.length > 0) && (
            <p className="font-mono text-[11px] leading-relaxed text-muted-foreground">
              {comparison.data.files_added.length > 0 && (
                <>+{comparison.data.files_added.join(", +")} </>
              )}
              {comparison.data.files_removed.length > 0 && (
                <>−{comparison.data.files_removed.join(", −")}</>
              )}
            </p>
          )}

          {(comparison.data.dependencies_added.length > 0 ||
            comparison.data.dependencies_removed.length > 0) && (
            <p className="text-xs text-muted-foreground">
              <span className="font-medium text-foreground">Dependencies</span>{" "}
              {comparison.data.dependencies_added.map((d) => `+${d}`).join(" ")}{" "}
              {comparison.data.dependencies_removed
                .map((d) => `−${d}`)
                .join(" ")}
            </p>
          )}

          {/* §20.4's distinction, one level up: an empty list because nobody
              recorded the history is not the same as nothing having landed. */}
          {comparison.data.commits_indexed ? (
            comparison.data.commits.length > 0 && (
              <p className="text-xs text-muted-foreground">
                {comparison.data.commits.length} commit
                {comparison.data.commits.length === 1 ? "" : "s"} between, most
                recent{" "}
                <span className="font-mono">
                  {comparison.data.commits[0].sha.slice(0, 8)}
                </span>{" "}
                — {comparison.data.commits[0].subject}
              </p>
            )
          ) : (
            <p className="text-xs text-muted-foreground">
              Commit history was not indexed for one of these snapshots, so the
              commits between them are unknown.
            </p>
          )}

          {comparison.data.symbols_added.length === 0 &&
            comparison.data.symbols_removed.length === 0 &&
            comparison.data.files_added.length === 0 &&
            comparison.data.files_removed.length === 0 && (
              <p className="text-xs text-muted-foreground">
                No files or symbols differ between these two commits. Lines may
                still have changed — that is what `git diff` is for.
              </p>
            )}
        </div>
      )}
    </section>
  );
}
