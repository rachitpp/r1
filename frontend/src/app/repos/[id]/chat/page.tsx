import { Suspense } from "react";

import { RequireAuth } from "@/components/auth/require-auth";
import { ChatView } from "@/components/chat/chat-view";
import { Skeleton } from "@/components/ui/skeleton";

export default async function ChatPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return (
    <RequireAuth
      title="Sign in to ask questions"
      description="Repositories are private to the account that indexed them."
    >
      {/* `ChatView` reads `?q=` via `useSearchParams`, which opts the subtree
          into client-side rendering and requires a boundary — without one the
          build fails rather than degrading. The fallback matches the pane's own
          pending state so the transition is not a second visual step. */}
      <Suspense
        fallback={
          <div className="page-container space-y-4 py-10">
            <Skeleton className="h-8 w-64" />
            <Skeleton className="h-40 w-full" />
          </div>
        }
      >
        <ChatView repoId={id} />
      </Suspense>
    </RequireAuth>
  );
}
