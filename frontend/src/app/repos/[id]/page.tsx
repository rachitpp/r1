import { RequireAuth } from "@/components/auth/require-auth";
import { RepoStatusView } from "@/components/repo/repo-status-view";

export default async function RepoPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return (
    <main className="page-container py-8">
      {/* The backend already answers 404 for a repo that is not yours (SPEC
          §13.5). This only decides what a signed-out visitor sees: an invitation
          to sign in, rather than "repo not found" for a repo that may well be
          theirs once they do. */}
      <RequireAuth
        title="Sign in to view this repository"
        description="Repositories are private to the account that indexed them."
      >
        <RepoStatusView repoId={id} />
      </RequireAuth>
    </main>
  );
}
