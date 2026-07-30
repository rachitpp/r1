"use client";

/**
 * `/` — submit form and repo list. Exported as two components rather than one
 * so the landing page can set the measured-numbers strip between them; both
 * halves still share the repos query (a successful submit updates the list
 * without a refetch).
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ChevronRight,
  Clock,
  FileCode2,
  GitCommitHorizontal,
  Github,
  Layers,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";

import { SignInButton } from "@/components/auth/user-menu";
import { StatusBadge } from "@/components/status-badge";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useUser } from "@/hooks/use-user";
import { ApiError, createRepo, listRepos, type RepoOut } from "@/lib/api";
import {
  shortSha,
  splitRepoName,
  strategyTag,
  stripStrategySuffix,
  timeAgo,
} from "@/lib/format";
import { cn } from "@/lib/utils";

/** Small, pure-Python repos that index quickly — a starting point for a visitor
 * who has no URL to hand. Clicking one fills the field; submitting is still
 * their call, because POST /repos re-queues a repo that is already indexed. */
const EXAMPLES = [
  "pallets/click",
  "psf/requests",
  "encode/starlette",
] as const;

/** Fallback monogram tints, drawn only from the palette's own three inks. Six
 * unrelated Tailwind pastels made the list look like a colour sampler; three
 * washes of colours already on the page read as one family. */
const MONOGRAM_TINTS = [
  "bg-[hsl(var(--primary)/0.12)] text-primary",
  "bg-[hsl(var(--sage)/0.14)] text-[hsl(var(--sage))]",
  "bg-[hsl(var(--ochre)/0.14)] text-[hsl(var(--ochre))]",
];

function tintFor(seed: string): string {
  let hash = 0;
  for (let i = 0; i < seed.length; i++) hash = (hash * 31 + seed.charCodeAt(i)) | 0;
  return MONOGRAM_TINTS[Math.abs(hash) % MONOGRAM_TINTS.length];
}

/** Owner avatar from GitHub, falling back to a monogram tile when the request
 * fails — offline, a 404 owner, or a blocked image host. */
function RepoAvatar({ owner, name }: { owner: string | null; name: string }) {
  const [failed, setFailed] = useState(false);
  if (owner && !failed) {
    return (
      // eslint-disable-next-line @next/next/no-img-element -- a remote avatar host we deliberately do not route through next/image
      <img
        src={`https://github.com/${owner}.png?size=80`}
        alt=""
        loading="lazy"
        onError={() => setFailed(true)}
        className="size-9 shrink-0 rounded-md border bg-muted object-cover"
      />
    );
  }
  return (
    <span
      aria-hidden
      className={cn(
        "flex size-9 shrink-0 items-center justify-center rounded-md font-mono text-sm font-semibold",
        tintFor(owner ?? name),
      )}
    >
      {(name[0] ?? "?").toUpperCase()}
    </span>
  );
}

function Meta({
  icon: Icon,
  children,
}: {
  icon: typeof FileCode2;
  children: React.ReactNode;
}) {
  return (
    <span className="inline-flex items-center gap-1">
      <Icon className="size-3.5 shrink-0 text-muted-foreground" />
      {children}
    </span>
  );
}

/** The one-line summary under a repo name, by lifecycle state (§10). */
function RepoMeta({ repo }: { repo: RepoOut }) {
  const p = repo.progress;
  const sha = shortSha(repo.head_sha);

  if (repo.status === "failed") {
    return (
      <span className="truncate text-destructive">
        {repo.error ?? "ingest failed"}
      </span>
    );
  }
  if (repo.status !== "ready") {
    const hint =
      repo.status === "embedding"
        ? `embedding ${p.chunks_embedded}/${p.chunks_total} chunks`
        : repo.status === "parsing"
          ? `parsing ${p.files_parsed}/${p.files_total || "…"} files`
          : "indexing…";
    return <span className="truncate">{hint}</span>;
  }
  return (
    <>
      <Meta icon={FileCode2}>{p.files_total} files</Meta>
      <Meta icon={Layers}>{p.chunks_total} chunks</Meta>
      {sha && (
        <Meta icon={GitCommitHorizontal}>
          <span className="font-mono">{sha}</span>
        </Meta>
      )}
      <Meta icon={Clock}>{timeAgo(repo.created_at)}</Meta>
    </>
  );
}

function RepoRow({ repo }: { repo: RepoOut }) {
  const displayName = stripStrategySuffix(repo.name);
  const { owner, name } = splitRepoName(displayName);
  const tag = strategyTag(repo.url);

  return (
    // A ledger row, not a floating card: the list is one sheet ruled by
    // hairlines, and hovering warms the row rather than levitating it. A stack
    // of identical drop-shadowed tiles is the most recognisable generated-UI
    // shape there is.
    <Link
      href={`/repos/${repo.id}`}
      className="group relative block focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
    >
      {/* Clay marker on the left edge, drawn on hover. */}
      <span
        aria-hidden
        className="absolute inset-y-0 left-0 w-0.5 origin-top scale-y-0 bg-primary transition-transform duration-200 group-hover:scale-y-100"
      />
      <div className="flex items-center gap-3.5 px-3.5 py-3 transition-colors duration-150 group-hover:bg-secondary/60">
        <RepoAvatar owner={owner} name={name} />

        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate font-mono text-sm font-medium">
              {owner && <span className="text-muted-foreground">{owner}/</span>}
              {name}
            </span>
            {tag && (
              <span className="shrink-0 rounded-sm border border-[hsl(var(--ochre)/0.35)] bg-[hsl(var(--ochre)/0.1)] px-1.5 py-px text-[11px] font-medium uppercase tracking-wide text-[hsl(var(--ochre))]">
                {tag}
                {/* The badge is shrink-0, so on a narrow screen the long form
                    would eat the repo name's width instead of its own. */}
                <span className="hidden sm:inline"> baseline</span>
              </span>
            )}
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
            <RepoMeta repo={repo} />
          </div>
        </div>

        <StatusBadge status={repo.status} />
        <ChevronRight className="size-4 shrink-0 text-muted-foreground/40 transition-all group-hover:translate-x-0.5 group-hover:text-muted-foreground" />
      </div>
    </Link>
  );
}

/**
 * The hero's submit slot. Signed in → the real form. Signed out → the primary
 * action stays *visible*: a real (disabled) input and a sign-in CTA, rather than
 * a card that hides what you came to do. A visitor sees the exact interaction
 * they'll get once signed in.
 */
export function RepoSubmit() {
  const { user, isLoading } = useUser();

  if (isLoading) {
    return (
      <div className="space-y-2" aria-busy>
        <Skeleton className="h-12 w-full" />
        <Skeleton className="h-3.5 w-2/3" />
      </div>
    );
  }

  if (user) return <SubmitForm />;

  return (
    <div>
      <div className="flex flex-col gap-2 sm:flex-row">
        <div className="relative flex-1">
          <Github
            aria-hidden
            className="pointer-events-none absolute left-3.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
          />
          <Input
            disabled
            placeholder="https://github.com/owner/repo"
            aria-label="GitHub repository URL"
            className="h-12 rounded-md border-input bg-card pl-10 font-mono text-sm shadow-none"
          />
        </div>
        <SignInButton className="h-12 w-full rounded-md px-7 text-sm sm:w-auto" />
      </div>
      <p className="mt-3 text-[13px] font-medium text-muted-foreground">
        Sign in with GitHub to index a repo — your repositories stay private to
        your account.
      </p>
    </div>
  );
}

export function SubmitForm() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const inputRef = useRef<HTMLInputElement>(null);
  const [url, setUrl] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);

  const submit = useMutation({
    mutationFn: createRepo,
    onSuccess: (repo) => {
      void queryClient.invalidateQueries({ queryKey: ["repos"] });
      router.push(`/repos/${repo.id}`);
    },
    onError: (err) => {
      setFieldError(
        err instanceof ApiError ? err.detail : "Something went wrong — try again.",
      );
    },
  });

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        setFieldError(null);
        // The button stays live even on an empty field — a primary action that
        // renders disabled at rest reads as broken. Validate on submit instead.
        if (!url.trim()) {
          setFieldError("Paste a GitHub repository URL to index.");
          inputRef.current?.focus();
          return;
        }
        submit.mutate(url.trim());
      }}
    >
      <div className="flex flex-col gap-2 sm:flex-row">
        <div className="relative flex-1">
          <Github
            aria-hidden
            className="pointer-events-none absolute left-3.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
          />
          <Input
            ref={inputRef}
            value={url}
            onChange={(e) => {
              setUrl(e.target.value);
              setFieldError(null);
            }}
            placeholder="https://github.com/owner/repo"
            aria-label="GitHub repository URL"
            aria-invalid={fieldError != null}
            className={cn(
              "h-12 rounded-md border-input bg-card pl-10 font-mono text-sm shadow-none",
              fieldError && "border-destructive focus-visible:ring-destructive",
            )}
          />
        </div>
        <Button
          type="submit"
          disabled={submit.isPending}
          className="h-12 w-full rounded-md px-7 text-sm sm:w-auto"
        >
          {submit.isPending ? "Indexing…" : "Index repo"}
        </Button>
      </div>

      {fieldError && (
        <p role="alert" className="mt-2 text-sm text-destructive">
          {fieldError}
        </p>
      )}

      {/* Examples read as an aside in prose — "or try …" — rather than as a row
          of pill buttons, which is the stock generated-hero furniture. */}
      <div className="mt-3 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
        <span>or try</span>
        {EXAMPLES.map((example, i) => (
          <span key={example} className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => {
                setUrl(`https://github.com/${example}`);
                setFieldError(null);
                inputRef.current?.focus();
              }}
              className="rounded-sm font-mono underline decoration-border decoration-dotted underline-offset-4 transition-colors hover:text-primary hover:decoration-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {example}
            </button>
            {i < EXAMPLES.length - 1 && <span aria-hidden>·</span>}
          </span>
        ))}
      </div>
    </form>
  );
}

export function RepoList() {
  const { user, isLoading: userLoading } = useUser();
  const repos = useQuery({
    queryKey: ["repos"],
    queryFn: listRepos,
    // The library is per-account (§13.6). Asking for it while signed out is a
    // guaranteed 401, which would render as "Could not load repositories" —
    // an error where the truth is simply that nobody is signed in.
    enabled: Boolean(user),
  });

  // Signed out: the hero already shows the sign-in card. A second one here
  // would be the same prompt twice on one screen.
  if (userLoading || !user) return null;

  if (repos.isPending) {
    return (
      <div className="divide-y rounded-md border bg-card">
        {[0, 1, 2].map((i) => (
          <div key={i} className="flex items-center gap-3.5 px-3.5 py-3">
            <Skeleton className="size-9 shrink-0 rounded-md" />
            <div className="flex-1 space-y-2">
              <Skeleton className="h-3.5 w-40" />
              <Skeleton className="h-3 w-56" />
            </div>
          </div>
        ))}
      </div>
    );
  }
  if (repos.isError) {
    return (
      <Alert variant="destructive">
        <AlertTitle>Could not load repositories</AlertTitle>
        <AlertDescription>
          {repos.error instanceof ApiError
            ? repos.error.detail
            : "Unexpected error."}
        </AlertDescription>
      </Alert>
    );
  }
  if (repos.data.length === 0) {
    return (
      <div className="rounded-md border border-dashed bg-card/40 px-6 py-12 text-center">
        <p className="display text-lg">Nothing indexed yet</p>
        <p className="mx-auto mt-1.5 max-w-xs text-sm text-muted-foreground">
          Paste a URL above, or pick one of the examples, and the first index
          will appear here.
        </p>
      </div>
    );
  }
  return (
    <>
      <div className="mb-3 flex items-baseline gap-3">
        <h2 className="display shrink-0 text-base font-semibold">
          Indexed repositories
        </h2>
        {/* Rule fills the gap between the heading and the count — an editorial
            contents-page device, and it costs nothing. */}
        <span aria-hidden className="h-px flex-1 bg-border" />
        <span className="shrink-0 font-mono text-xs tabular-nums text-muted-foreground">
          {repos.data.length}
        </span>
      </div>
      <div className="divide-y overflow-hidden rounded-md border bg-card">
        {repos.data.map((repo) => (
          <RepoRow key={repo.id} repo={repo} />
        ))}
      </div>
    </>
  );
}

