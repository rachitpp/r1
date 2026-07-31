"use client";

/**
 * `/repos/[id]` — repo overview & indexing page (§10 states).
 *
 * Polls while a worker owns the job and stops on `ready`/`failed` — the
 * `refetchInterval` callback reads the latest data, so the poll turns itself
 * off. The three lifecycle states get three distinct panels: a live icon
 * stepper while indexing, a stats + CTA overview once ready, and an error +
 * retry when it failed. Retry on a failed repo is `POST /repos` with the same
 * URL: the backend re-queues any non-in-flight row (Phase 5 pre-authorized).
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  Check,
  Clock,
  Download,
  ExternalLink,
  FileCode2,
  GitCommitHorizontal,
  Layers,
  MessageSquare,
  RotateCcw,
  Waypoints,
} from "lucide-react";
import Link from "next/link";
import { Fragment, useState } from "react";

import { ArchitecturePanel } from "@/components/architecture-panel";
import { OverviewPanel } from "@/components/overview-panel";
import { StatusBadge } from "@/components/status-badge";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
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
import {
  shortSha,
  splitRepoName,
  stripStrategySuffix,
  timeAgo,
} from "@/lib/format";
import { cn } from "@/lib/utils";

const PIPELINE: readonly RepoStatus[] = [
  "queued",
  "cloning",
  "parsing",
  "linking",
  "embedding",
  "ready",
];

const STAGE: Record<
  RepoStatus,
  { label: string; verb: string; icon: typeof Clock }
> = {
  queued: { label: "Queued", verb: "Waiting for a worker", icon: Clock },
  cloning: { label: "Clone", verb: "Cloning the repository", icon: Download },
  parsing: {
    label: "Parse",
    verb: "Parsing files into AST chunks",
    icon: FileCode2,
  },
  linking: {
    label: "Link",
    verb: "Building the symbol graph",
    icon: Waypoints,
  },
  embedding: {
    label: "Embed",
    verb: "Embedding chunks for search",
    icon: Layers,
  },
  ready: { label: "Ready", verb: "Indexed and ready for questions", icon: Check },
  failed: { label: "Failed", verb: "Ingest failed", icon: Clock },
};

/** Owner avatar from GitHub, falling back to a monogram tile. */
function OwnerAvatar({ owner, name }: { owner: string | null; name: string }) {
  const [failed, setFailed] = useState(false);
  if (owner && !failed) {
    return (
      // eslint-disable-next-line @next/next/no-img-element -- remote avatar host, deliberately not via next/image
      <img
        src={`https://github.com/${owner}.png?size=72`}
        alt=""
        loading="lazy"
        onError={() => setFailed(true)}
        className="size-8 shrink-0 rounded-md border bg-muted object-cover"
      />
    );
  }
  return (
    <span
      aria-hidden
      className="flex size-8 shrink-0 items-center justify-center rounded-md bg-[hsl(var(--primary)/0.12)] font-mono text-sm font-semibold text-primary"
    >
      {(name[0] ?? "?").toUpperCase()}
    </span>
  );
}

function Meta({
  icon: Icon,
  children,
}: {
  icon: typeof Clock;
  children: React.ReactNode;
}) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <Icon className="size-3.5 shrink-0 text-muted-foreground" />
      {children}
    </span>
  );
}

/** The pipeline as an icon stepper: sage = done, clay = in flight, muted = to
 * come. Labels hide below `sm`, where six of them would collide. */
function Stepper({ status }: { status: RepoStatus }) {
  const activeIdx = PIPELINE.indexOf(status);
  return (
    <ol className="flex items-start">
      {PIPELINE.map((state, i) => {
        const done = activeIdx > i || status === "ready";
        const active = state === status && status !== "ready";
        const { icon: Icon, label } = STAGE[state];
        return (
          <Fragment key={state}>
            <li className="flex shrink-0 flex-col items-center gap-1.5">
              <span
                className={cn(
                  "flex size-7 items-center justify-center rounded-full border-2 transition-colors",
                  done &&
                    "border-[hsl(var(--sage))] bg-[hsl(var(--sage)/0.14)] text-[hsl(var(--sage))]",
                  active && "border-primary bg-primary/10 text-primary",
                  !done &&
                    !active &&
                    "border-border bg-card text-muted-foreground/50",
                )}
              >
                {done ? (
                  <Check className="size-3.5" />
                ) : (
                  <Icon className={cn("size-3.5", active && "animate-pulse")} />
                )}
              </span>
              <span
                className={cn(
                  "hidden font-mono text-[10px] sm:block",
                  active
                    ? "font-semibold text-foreground"
                    : done
                      ? "text-foreground/70"
                      : "text-muted-foreground/60",
                )}
              >
                {label}
              </span>
            </li>
            {i < PIPELINE.length - 1 && (
              <span
                aria-hidden
                className={cn(
                  "mt-[13px] h-0.5 flex-1 rounded-full transition-colors",
                  activeIdx > i ? "bg-[hsl(var(--sage))]" : "bg-border",
                )}
              />
            )}
          </Fragment>
        );
      })}
    </ol>
  );
}

function Bar({
  label,
  done,
  total,
}: {
  label: string;
  done: number;
  total: number;
}) {
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;
  return (
    <div className="space-y-1">
      <div className="flex items-baseline justify-between text-xs">
        <span className="font-medium text-foreground">{label}</span>
        <span className="font-mono tabular-nums text-muted-foreground">
          {done}/{total || "—"}
          <span className="ml-2 text-foreground/70">{pct}%</span>
        </span>
      </div>
      <Progress value={pct} />
    </div>
  );
}

function Stat({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <dt className="display text-xl font-semibold leading-none tabular-nums sm:text-2xl">
        {children}
      </dt>
      <dd className="mt-1 font-mono text-[10px] uppercase tracking-[0.1em] text-muted-foreground">
        {label}
      </dd>
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
      <div className="space-y-5">
        <div className="flex items-center gap-2.5">
          <Skeleton className="size-8 shrink-0 rounded-md" />
          <div className="flex-1 space-y-1.5">
            <Skeleton className="h-4 w-48" />
            <Skeleton className="h-3 w-64" />
          </div>
        </div>
        <Skeleton className="h-36 w-full rounded-lg" />
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

  const data: RepoOut = repo.data;
  const displayName = stripStrategySuffix(data.name);
  const { owner, name } = splitRepoName(displayName);
  const sha = shortSha(data.head_sha);
  const p = data.progress;
  const isReady = data.status === "ready";
  const isFailed = data.status === "failed";
  const host = data.url.replace(/^https?:\/\//, "");

  return (
    <div className="space-y-5">
      {/* Header — back, identity, status. */}
      <header className="flex items-start justify-between gap-4">
        <div className="flex min-w-0 items-start gap-2.5">
          <Link
            href="/"
            aria-label="Back to your repositories"
            title="Back to your repositories"
            className="mt-0.5 shrink-0 rounded-sm p-1 text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <ArrowLeft className="size-4" />
          </Link>
          <OwnerAvatar owner={owner} name={name} />
          <div className="min-w-0 space-y-0.5">
            <h1 className="truncate font-mono text-sm font-semibold sm:text-base">
              {owner && <span className="text-muted-foreground">{owner}/</span>}
              {name}
            </h1>
            <a
              href={data.url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex max-w-full items-center gap-1 truncate rounded-sm text-xs text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <ExternalLink className="size-3 shrink-0" />
              <span className="truncate">{host}</span>
            </a>
          </div>
        </div>
        <StatusBadge status={data.status} />
      </header>

      {/* Ready — the overview a reader lands on to start chatting. */}
      {isReady && (
        <section className="space-y-4 rounded-lg border bg-card p-4 sm:p-5">
          <div>
            <p className="eyebrow">Indexed</p>
            <h2 className="display mt-2 text-lg font-semibold sm:text-xl">
              Ready to answer questions.
            </h2>
            <p className="mt-1.5 max-w-md text-[13px] leading-relaxed text-muted-foreground">
              Chunked on AST boundaries, embedded, and cross-linked into a symbol
              graph. Every answer cites the source, and each citation opens to
              the exact lines.
            </p>
          </div>

          <dl className="grid grid-cols-2 gap-6 border-y py-3.5">
            <Stat label="Files indexed">{p.files_total}</Stat>
            <Stat label="Code chunks">{p.chunks_total}</Stat>
          </dl>

          <div className="flex flex-wrap items-center gap-x-5 gap-y-1.5 text-xs text-muted-foreground">
            {sha && (
              <Meta icon={GitCommitHorizontal}>
                <span className="font-mono">{sha}</span>
              </Meta>
            )}
            <Meta icon={Clock}>Indexed {timeAgo(data.created_at)}</Meta>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Button asChild>
              <Link href={`/repos/${repoId}/chat`}>
                <MessageSquare className="size-4" />
                Ask about this codebase
              </Link>
            </Button>
            <Button asChild variant="outline">
              <a href={data.url} target="_blank" rel="noreferrer">
                <ExternalLink className="size-4" />
                View on GitHub
              </a>
            </Button>
          </div>
        </section>
      )}

      {/* The generated guide (§19). First thing under the CTA, because it is
          the answer to "what do I even ask?" — which is the question a blank
          chat box does not answer. */}
      {isReady && <OverviewPanel repoId={repoId} />}

      {/* The module map (§18.2). Below the CTA rather than above it: chatting is
          still the primary action, and this is orientation for a reader who does
          not yet know what to ask. Mounted only when ready — the rollup of a
          half-built graph would be a map of a partial repo. */}
      {isReady && <ArchitecturePanel repoId={repoId} />}

      {/* In flight — the live process. */}
      {!isReady && !isFailed && (
        <section className="space-y-5 rounded-lg border bg-card p-4 sm:p-5">
          <div>
            <p className="eyebrow">Indexing</p>
            <h2 className="display mt-2 text-lg font-semibold sm:text-xl">
              {STAGE[data.status].verb}…
            </h2>
            <p className="mt-1.5 text-[13px] text-muted-foreground">
              This page updates itself — leave it open, and it switches to the
              chat the moment indexing finishes.
            </p>
          </div>
          <Stepper status={data.status} />
          <div className="space-y-3.5 border-t pt-4">
            <Bar
              label="Files parsed"
              done={p.files_parsed}
              total={p.files_total}
            />
            <Bar
              label="Chunks embedded"
              done={p.chunks_embedded}
              total={p.chunks_total}
            />
          </div>
        </section>
      )}

      {/* Failed — the error and the one-click retry. */}
      {isFailed && (
        <section className="space-y-4 rounded-lg border border-destructive/30 bg-destructive/[0.04] p-4 sm:p-5">
          <div>
            <h2 className="display text-base font-semibold sm:text-lg">
              Indexing failed
            </h2>
            <p className="mt-1.5 text-[13px] text-muted-foreground">
              The ingest didn&apos;t complete. Here is what the worker reported:
            </p>
          </div>
          <pre className="overflow-x-auto rounded-md border bg-card p-3 font-mono text-xs text-destructive">
            {data.error ?? "no error recorded"}
          </pre>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              onClick={() => retry.mutate(data.url)}
              disabled={retry.isPending}
            >
              <RotateCcw className="size-4" />
              {retry.isPending ? "Re-queuing…" : "Retry ingest"}
            </Button>
            <Button asChild variant="outline">
              <a href={data.url} target="_blank" rel="noreferrer">
                <ExternalLink className="size-4" />
                View on GitHub
              </a>
            </Button>
          </div>
          {retry.isError && (
            <p role="alert" className="text-sm text-destructive">
              {retry.error instanceof ApiError
                ? retry.error.detail
                : "Retry failed — is the backend running?"}
            </p>
          )}
        </section>
      )}
    </div>
  );
}
