import { Button } from "@/components/ui/button";

export default function Home() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-6 p-8">
      <h1 className="text-3xl font-semibold tracking-tight">
        Codebase Onboarding Assistant
      </h1>
      <p className="max-w-md text-center text-muted-foreground">
        Phase 0 scaffold. The submit and chat experiences arrive in Phase 5.
      </p>
      <Button>Get started</Button>
    </main>
  );
}
