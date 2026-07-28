import { RepoStatusView } from "@/components/repo-status-view";

export default async function RepoPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return (
    <main className="page-container py-10">
      <RepoStatusView repoId={id} />
    </main>
  );
}
