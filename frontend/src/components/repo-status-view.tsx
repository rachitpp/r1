"use client";

/**
 * `/repos/[id]` — the indexing progress page (§10 states).
 *
 * Polls while a worker owns the job and stops on `ready`/`failed` — the
 * `refetchInterval` callback reads the latest data, so the poll turns itself
 * off. Retry on a failed repo is `POST /repos` with the same URL: the backend
 * re-queues any non-in-flight row (Phase 5 pre-authorized exception).
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";

import { StatusBadge } from "@/components/status-badge";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ApiError,
  IN_FLIGHT_STATUSES,
  createRepo,
  getRepo,
  type RepoOut,
  type RepoStatus,
} from "@/lib/api";

const PIPELINE: readonly RepoStatus[] = [
  "queued",
  "cloning",
  "parsing",
  "linking",
  "embedding",
  "ready",
];

const STATE_LABEL: Record<RepoStatus, string> = {
  queued: "Waiting for a worker…",
  cloning: "Cloning the repository…",
  parsing: "Parsing files into AST chunks…",
  linking: "Building the symbol graph (imports & calls)…",
  embedding: "Embedding chunks…",
  ready: "Indexed and ready for questions.",
  failed: "Ingest failed.",
};

function PipelineTrack({ status }: { status: RepoStatus }) {
  const activeIdx = PIPELINE.indexOf(status);
  return (
    <ol className="flex flex-wrap items-center gap-1.5 text-xs">
      {PIPELINE.map((state, i) => {
        const isDone = activeIdx > i || status === "ready";
        const isActive = state === status && status !== "ready";
        return (
          <li key={state} className="flex items-center gap-1.5">
            {i > 0 && <span className="text-muted-foreground/40">→</span>}
            <span
              className={
                isActive
                  ? "font-semibold text-foreground"
                  : isDone
                    ? "text-foreground/70"
                    : "text-muted-foreground/50"
              }
            >
              {state}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

function ProgressBars({ repo }: { repo: RepoOut }) {
  const p = repo.progress;
  const filesPct = p.files_total > 0 ? (p.files_parsed / p.files_total) * 100 : 0;
  const chunksPct =
    p.chunks_total > 0 ? (p.chunks_embedded / p.chunks_total) * 100 : 0;
  return (
    <div className="space-y-4">
      <div className="space-y-1.5">
        <div className="flex justify-between text-xs text-muted-foreground">
          <span>Files parsed</span>
          <span className="font-mono">
            {p.files_parsed}/{p.files_total || "—"}
          </span>
        </div>
        <Progress value={filesPct} />
      </div>
      <div className="space-y-1.5">
        <div className="flex justify-between text-xs text-muted-foreground">
          <span>Chunks embedded</span>
          <span className="font-mono">
            {p.chunks_embedded}/{p.chunks_total || "—"}
          </span>
        </div>
        <Progress value={chunksPct} />
      </div>
    </div>
  );
}

export function RepoStatusView({ repoId }: { repoId: string }) {
  const queryClient = useQueryClient();
  const repo = useQuery({
    queryKey: ["repo", repoId],
    queryFn: () => getRepo(repoId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status && IN_FLIGHT_STATUSES.includes(status) ? 1500 : false;
    },
    retry: (count, err) =>
      !(err instanceof ApiError && err.status === 404) && count < 1,
  });

  const retry = useMutation({
    mutationFn: (url: string) => createRepo(url),
    onSuccess: (updated) => {
      queryClient.setQueryData(["repo", repoId], updated);
    },
  });

  if (repo.isPending) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }
  if (repo.isError) {
    const notFound = repo.error instanceof ApiError && repo.error.status === 404;
    return (
      <Alert variant="destructive">
        <AlertTitle>{notFound ? "Repository not found" : "Error"}</AlertTitle>
        <AlertDescription className="space-y-3">
          <p>
            {notFound
              ? "No repository with this id exists. It may have been removed."
              : repo.error instanceof ApiError
                ? repo.error.detail
                : "Unexpected error."}
          </p>
          <Button asChild variant="outline" size="sm">
            <Link href="/">Back to repositories</Link>
          </Button>
        </AlertDescription>
      </Alert>
    );
  }

  const data = repo.data;
  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 space-y-1">
          <h1 className="truncate font-mono text-xl font-semibold">
            {data.name}
          </h1>
          <p className="truncate text-xs text-muted-foreground">
            {data.url}
            {data.head_sha ? ` @ ${data.head_sha.slice(0, 10)}` : ""}
          </p>
        </div>
        <StatusBadge status={data.status} />
      </div>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm font-medium">
            {STATE_LABEL[data.status]}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-6">
          <PipelineTrack status={data.status} />
          {data.status !== "ready" && data.status !== "failed" && (
            <ProgressBars repo={data} />
          )}
          {data.status === "ready" && (
            <div className="flex items-center justify-between gap-4">
              <p className="text-sm text-muted-foreground">
                {data.progress.files_total} files ·{" "}
                {data.progress.chunks_total} chunks indexed
              </p>
              <Button asChild size="lg">
                <Link href={`/repos/${repoId}/chat`}>
                  Ask about this codebase
                </Link>
              </Button>
            </div>
          )}
          {data.status === "failed" && (
            <div className="space-y-4">
              <Alert variant="destructive">
                <AlertTitle>Ingest failed</AlertTitle>
                <AlertDescription className="font-mono text-xs">
                  {data.error ?? "no error recorded"}
                </AlertDescription>
              </Alert>
              <Button
                onClick={() => retry.mutate(data.url)}
                disabled={retry.isPending}
              >
                {retry.isPending ? "Re-queuing…" : "Retry ingest"}
              </Button>
              {retry.isError && (
                <p role="alert" className="text-sm text-red-600">
                  {retry.error instanceof ApiError
                    ? retry.error.detail
                    : "Retry failed — is the backend running?"}
                </p>
              )}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
