"use client";

/**
 * The header's identity slot (SPEC §13): a sign-in button, or who you are.
 *
 * Renders nothing at all while the first `/auth/me` is in flight. A button
 * that says "Sign in" for a moment and then becomes your avatar is a worse
 * flicker than a brief gap, because the wrong state is legible and the gap is
 * not.
 */

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { useLogout, useUser } from "@/hooks/use-user";
import { loginUrl } from "@/lib/api";

function GithubIcon() {
  return (
    <svg viewBox="0 0 16 16" fill="currentColor" aria-hidden className="size-4">
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82a7.42 7.42 0 0 1 2-.27c.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8Z" />
    </svg>
  );
}

/**
 * A full-page navigation, not a fetch (see `loginUrl`). Rendered as an anchor
 * so it behaves like one for middle-click, keyboard, and screen readers.
 */
export function SignInButton({ className }: { className?: string }) {
  return (
    <Button asChild size="sm" className={className}>
      <a href={loginUrl}>
        <GithubIcon />
        Sign in with GitHub
      </a>
    </Button>
  );
}

export function UserMenu() {
  const { user, isLoading } = useUser();
  const signOut = useLogout();
  const [avatarFailed, setAvatarFailed] = useState(false);

  if (isLoading) return null;
  if (!user) return <SignInButton />;

  return (
    <div className="flex items-center gap-2">
      {user.avatar_url && !avatarFailed ? (
        // Plain <img> with a fallback, matching `RepoAvatar` — a remote avatar
        // host deliberately not routed through next/image.
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={user.avatar_url}
          alt=""
          width={22}
          height={22}
          onError={() => setAvatarFailed(true)}
          className="size-[22px] rounded-full object-cover ring-1 ring-border"
        />
      ) : null}
      <span className="hidden text-xs font-medium text-muted-foreground sm:inline">
        {user.login}
      </span>
      <Button
        variant="ghost"
        size="sm"
        onClick={signOut}
        className="h-7 px-2 text-xs text-muted-foreground hover:text-foreground"
      >
        Sign out
      </Button>
    </div>
  );
}
