"use client";

/**
 * App-level client providers. The layout stays a server component; this is the
 * one client boundary it renders (CLAUDE.md frontend conventions).
 */

import {
  QueryCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { useState } from "react";

import { USER_QUERY_KEY } from "@/hooks/use-user";
import { ApiError } from "@/lib/api-client";

export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(() => {
    // Self-reference: the cache's error handler needs the client it belongs to,
    // and both are created here. `qc` is assigned before any query can run, so
    // the closure always sees it.
    let qc: QueryClient;
    const queryCache = new QueryCache({
      // A 401 from *any* authenticated query means the session ended — expired,
      // or signed out in another tab. Reset the cached user to null so every
      // `RequireAuth` gate re-renders as signed-out; without this a dead session
      // surfaces as a repo "error" instead of a sign-in prompt.
      onError: (error) => {
        if (error instanceof ApiError && error.status === 401) {
          qc.setQueryData(USER_QUERY_KEY, null);
        }
      },
    });
    qc = new QueryClient({
      queryCache,
      defaultOptions: {
        queries: {
          // The API is local; failures are "backend not running", which a
          // retry storm does not fix. One retry covers a blip.
          retry: 1,
          refetchOnWindowFocus: false,
        },
      },
    });
    return qc;
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
