"use client";

/**
 * Gate for anything that needs a session (SPEC §13.5).
 *
 * Client-side rather than Next middleware, deliberately. Middleware can only
 * read cookies scoped to the *frontend's* origin; the session cookie is set by
 * the API. On localhost those happen to be the same site (cookies ignore the
 * port), so middleware would appear to work — and then silently fail the first
 * time the API is deployed to its own domain. Asking `/auth/me` is the check
 * that behaves identically in both.
 *
 * The backend is the actual enforcement (§13.5); this only decides what to
 * render. A user who defeats it reaches endpoints that answer 404.
 */

import { SignInButton } from "@/components/auth/user-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { useUser } from "@/hooks/use-user";

export function RequireAuth({
  children,
  title = "Sign in to continue",
  description = "Your repositories are private to your account.",
}: {
  children: React.ReactNode;
  title?: string;
  description?: string;
}) {
  const { user, isLoading, isUnreachable, error } = useUser();

  if (isLoading) {
    return (
      <div className="space-y-3" aria-busy>
        <Skeleton className="h-9 w-full" />
        <Skeleton className="h-9 w-2/3" />
      </div>
    );
  }

  if (isUnreachable) {
    // Distinct from signed-out on purpose: offering "Sign in with GitHub" when
    // the API is simply down sends the user through a redirect chain that
    // cannot succeed, and tells them the wrong thing about why.
    return (
      <div className="rounded-lg border border-border bg-card p-5 text-sm">
        <p className="font-medium">Can&apos;t reach the API</p>
        <p className="mt-1 text-muted-foreground">
          The backend isn&apos;t responding. Start it and reload.
        </p>
      </div>
    );
  }

  if (error) {
    // Reachable but erroring (a 5xx). Not "down", and not "signed out" — say so,
    // and quote the request id so the failure can be found in the server log.
    return (
      <div className="rounded-lg border border-border bg-card p-5 text-sm">
        <p className="font-medium">Couldn&apos;t verify your session</p>
        <p className="mt-1 text-muted-foreground">
          The API returned an error. Reload to try again.
          {error.requestId ? (
            <>
              {" "}
              <span className="font-mono text-xs">({error.requestId})</span>
            </>
          ) : null}
        </p>
      </div>
    );
  }

  if (!user) {
    return (
      <div className="rounded-lg border border-border bg-card p-6">
        <p className="display text-base font-semibold">{title}</p>
        <p className="mt-1.5 text-sm text-muted-foreground">{description}</p>
        <SignInButton className="mt-4" />
      </div>
    );
  }

  return <>{children}</>;
}
