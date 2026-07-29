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

import { getMe, logout, type UserOut } from "@/lib/api";

export const USER_QUERY_KEY = ["auth", "me"] as const;

export interface UseUser {
  user: UserOut | null;
  /** First load only. Re-checks in the background do not flip this back on. */
  isLoading: boolean;
  /** The API could not be reached at all — distinct from "signed out". */
  isUnreachable: boolean;
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
  return {
    user: data ?? null,
    isLoading: isPending,
    isUnreachable: Boolean(error),
  };
}

export function useLogout(): () => void {
  const queryClient = useQueryClient();
  const { mutate } = useMutation({
    mutationFn: logout,
    // Clear *everything*, not just the user: the repo list and every repo
    // detail in the cache belong to the session that just ended, and leaving
    // them would show the next visitor the previous one's library.
    onSettled: () => queryClient.clear(),
  });
  return () => mutate();
}
