"use client";

/**
 * `/` — submit form + repo list. One client component: both halves share the
 * repos query (a successful submit updates the list without a refetch).
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { StatusBadge } from "@/components/status-badge";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError, createRepo, listRepos, type RepoOut } from "@/lib/api";

function progressHint(repo: RepoOut): string {
  const p = repo.progress;
  switch (repo.status) {
    case "ready":
      return `${p.files_total} files · ${p.chunks_total} chunks`;
    case "embedding":
      return `embedding ${p.chunks_embedded}/${p.chunks_total} chunks`;
    case "parsing":
      return `parsing ${p.files_parsed}/${p.files_total || "…"} files`;
    case "failed":
      return repo.error ?? "ingest failed";
    default:
      return "indexing…";
  }
}

function SubmitForm() {
  const router = useRouter();
  const queryClient = useQueryClient();
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
      className="mb-8"
      onSubmit={(e) => {
        e.preventDefault();
        setFieldError(null);
        if (url.trim()) submit.mutate(url.trim());
      }}
    >
      <div className="flex gap-2">
        <Input
          value={url}
          onChange={(e) => {
            setUrl(e.target.value);
            setFieldError(null);
          }}
          placeholder="https://github.com/owner/repo"
          aria-label="GitHub repository URL"
          aria-invalid={fieldError != null}
          className="font-mono text-sm"
        />
        <Button type="submit" disabled={submit.isPending || !url.trim()}>
          {submit.isPending ? "Submitting…" : "Index repo"}
        </Button>
      </div>
      {fieldError && (
        <p role="alert" className="mt-2 text-sm text-red-600">
          {fieldError}
        </p>
      )}
    </form>
  );
}

function RepoList() {
  const repos = useQuery({ queryKey: ["repos"], queryFn: listRepos });

  if (repos.isPending) {
    return (
      <div className="space-y-3">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-16 w-full" />
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
      <p className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">
        No repositories yet — submit one above to get started.
      </p>
    );
  }
  return (
    <div className="space-y-3">
      {repos.data.map((repo) => (
        <Link key={repo.id} href={`/repos/${repo.id}`} className="block">
          <Card className="transition-colors hover:bg-muted/50">
            <CardContent className="flex items-center justify-between gap-4 p-4">
              <div className="min-w-0">
                <div className="truncate font-mono text-sm font-medium">
                  {repo.name}
                </div>
                <div className="truncate text-xs text-muted-foreground">
                  {progressHint(repo)}
                </div>
              </div>
              <StatusBadge status={repo.status} />
            </CardContent>
          </Card>
        </Link>
      ))}
    </div>
  );
}

export function RepoDashboard() {
  return (
    <>
      <SubmitForm />
      <RepoList />
    </>
  );
}
