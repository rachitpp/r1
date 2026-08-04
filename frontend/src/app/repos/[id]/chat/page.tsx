import { RequireAuth } from "@/components/auth/require-auth";
import { ChatView } from "@/components/chat/chat-view";

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
      {/* The Suspense boundary `ChatView` needs for `useSearchParams` now comes
          from this segment's `loading.tsx` — a Next convention doing the job a
          hand-rolled boundary was doing here. */}
      <ChatView repoId={id} />
    </RequireAuth>
  );
}
