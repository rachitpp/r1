import { RepoDashboard } from "@/components/repo-dashboard";

export default function Home() {
  return (
    <main className="mx-auto max-w-3xl px-4 py-10">
      <div className="mb-8 space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">Repositories</h1>
        <p className="text-sm text-muted-foreground">
          Submit a public Python GitHub repo, wait for indexing, then ask it
          questions.
        </p>
      </div>
      <RepoDashboard />
    </main>
  );
}
