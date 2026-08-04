"use client";

/**
 * The signed-in user (SPEC §13).
 *
 * Server state, so it goes through TanStack Query like everything else that is
 * not chat (CLAUDE.md frontend conventions) — one shared cache entry, so the
 * header, the landing page, and every guard read the same answer instead of
 * each making their own `/auth/me` call.
 *
 * Deliberately *not* a React context: the session lives in an HttpOnly cookie
 * the browser holds, and a context would be a second copy of that truth which
 * could disagree with it.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";

import { getMe, logout } from "@/lib/api";
import { ApiError } from "@/lib/api-client";
import type { UserOut } from "@/lib/api-types";
import { CHAT_STORAGE_PREFIX } from "@/lib/chat-types";

export const USER_QUERY_KEY = ["auth", "me"] as const;

export interface UseUser {
  user: UserOut | null;
  /** First load only. Re-checks in the background do not flip this back on. */
  isLoading: boolean;
  /** The API could not be reached at all (a connection failure) — distinct from
   * a signed-out 401 (translated to `null`) and from a server error (`error`). */
  isUnreachable: boolean;
  /** A non-connection failure of the session check, e.g. a 5xx. Null otherwise —
   * so the gate can say "something went wrong" rather than mislabel a reachable
   * but erroring API as "not responding". */
  error: ApiError | null;
}

export function useUser(): UseUser {
  const { data, isPending, error } = useQuery({
    queryKey: USER_QUERY_KEY,
    queryFn: getMe,
    // A signed-out visitor is `null`, not a failure, so this rarely rejects.
    // When it does the backend is down, and retrying does not fix that.
    retry: false,
    staleTime: 5 * 60_000,
  });
  // getMe already turns 401 into `null`, so any error here is a real failure.
  // status 0 is the sentinel for "the fetch itself threw" (API unreachable);
  // any other status is a reachable API that answered with an error.
  const apiError = error instanceof ApiError ? error : null;
  const unreachable = error != null && (apiError == null || apiError.status === 0);
  return {
    user: data ?? null,
    isLoading: isPending,
    isUnreachable: unreachable,
    error: unreachable ? null : apiError,
  };
}

/**
 * Drop every saved chat transcript. `queryClient.clear()` only empties the
 * TanStack cache; conversations live in sessionStorage under
 * `CHAT_STORAGE_PREFIX` and would otherwise survive a sign-out and be restored
 * for whoever signs in next on this tab — the same leak `clear()` exists to
 * prevent, in the one store it does not reach.
 */
function clearChatStorage(): void {
  if (typeof window === "undefined") return;
  try {
    const doomed: string[] = [];
    for (let i = 0; i < window.sessionStorage.length; i++) {
      const key = window.sessionStorage.key(i);
      if (key?.startsWith(CHAT_STORAGE_PREFIX)) doomed.push(key);
    }
    for (const key of doomed) window.sessionStorage.removeItem(key);
  } catch {
    // private-mode or quota access failure: nothing durable to clear anyway.
  }
}

export function useLogout(): () => void {
  const queryClient = useQueryClient();
  const router = useRouter();
  const { mutate } = useMutation({
    mutationFn: logout,
    // Tear the session down, then *leave the protected route*. Without the
    // redirect, signing out on a gated page (e.g. a repo chat) re-gates in
    // place and drops you on a sign-in card at the same URL, which reads as
    // "still on the page". Clear both stores the ended session owned — the
    // TanStack cache and the sessionStorage transcripts it cannot reach — then
    // navigate home, where a signed-out visitor belongs.
    onSettled: () => {
      queryClient.clear();
      clearChatStorage();
      router.push("/");
    },
  });
  return () => mutate();
}
