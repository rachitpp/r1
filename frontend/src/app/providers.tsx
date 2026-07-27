"use client";

/**
 * App-level client providers. The layout stays a server component; this is the
 * one client boundary it renders (CLAUDE.md frontend conventions).
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";

export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            // The API is local; failures are "backend not running", which a
            // retry storm does not fix. One retry covers a blip.
            retry: 1,
            refetchOnWindowFocus: false,
          },
        },
      }),
  );
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
