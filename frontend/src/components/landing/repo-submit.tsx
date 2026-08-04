"use client";

/**
 * `/` — the hero's submit slot.
 *
 * Split from the repo list (`repo-list.tsx`) rather than living beside it in
 * one `repo-dashboard` module: the landing page sets the measured-numbers strip
 * between the two, so they were never rendered as a unit, and the file's name
 * described a component that did not exist. They still share the `["repos"]`
 * query — a successful submit invalidates it, so the list updates without a
 * refetch of its own.
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Github } from "lucide-react";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";

import { SignInButton } from "@/components/auth/user-menu";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useUser } from "@/hooks/use-user";
import { createRepo } from "@/lib/api";
import { ApiError } from "@/lib/api-client";
import { cn } from "@/lib/utils";

/** Small, pure-Python repos that index quickly — a starting point for a visitor
 * who has no URL to hand. Clicking one fills the field; submitting is still
 * their call, because POST /repos re-queues a repo that is already indexed. */
const EXAMPLES = [
  "pallets/click",
  "psf/requests",
  "encode/starlette",
] as const;

/**
 * Signed in → the real form. Signed out → the primary action stays *visible*:
 * a real (disabled) input and a sign-in CTA, rather than a card that hides what
 * you came to do. A visitor sees the exact interaction they'll get once signed
 * in.
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

/** The signed-in half of `RepoSubmit`. Private: nothing renders it directly. */
function SubmitForm() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const inputRef = useRef<HTMLInputElement>(null);
  const [url, setUrl] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);

  const submit = useMutation({
    // Wrapped, not passed bare: TanStack hands the mutation function a context
    // as its second argument, and `createRepo` now takes an optional `rev`
    // there (SPEC §28.3).
    mutationFn: (url: string) => createRepo(url),
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
