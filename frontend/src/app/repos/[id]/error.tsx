"use client";

/**
 * Segment error boundary for `/repos/[id]` and everything below it.
 *
 * This catches what the in-component states cannot: a render that threw rather
 * than an API call that failed. `RepoStatusView` and `ChatView` already handle
 * their own `ApiError`s with copy that names the actual problem, so anything
 * reaching here is a bug, not a backend state — hence the deliberately plain
 * wording and a `reset()` retry rather than a diagnosis.
 *
 * `digest` is the server-side hash Next attaches to production errors; the real
 * message stays on the server, exactly as it does for the API's 5xx bodies.
 */

import { Button } from "@/components/ui/button";

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <main className="page-container py-16">
      <div className="mx-auto max-w-md rounded-lg border border-border bg-card p-6">
        <p className="display text-base font-semibold">Something went wrong</p>
        <p className="mt-1.5 text-sm text-muted-foreground">
          This page failed to render. Trying again is usually enough; if it
          isn&apos;t, reload.
          {error.digest ? (
            <>
              {" "}
              <span className="font-mono text-xs">({error.digest})</span>
            </>
          ) : null}
        </p>
        <Button onClick={reset} size="sm" className="mt-4">
          Try again
        </Button>
      </div>
    </main>
  );
}
