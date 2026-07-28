import { RepoDashboard } from "@/components/repo-dashboard";

/**
 * Every figure here is measured and lives in README.md ("The comparison").
 * Retrieval is the pipeline's own number on the 20 frozen questions — it is not
 * a claim that AST chunking beats the naive baseline, which it ties at hit@10.
 */
const STATS: { value: string; label: string }[] = [
  { value: "0.95", label: "hit@10 · 20 frozen questions" },
  { value: "AST", label: "tree-sitter chunk boundaries" },
  { value: "file:line", label: "clickable citations on every answer" },
];

function Hero() {
  return (
    <section className="pb-7 pt-8 sm:pt-10">
      <h1 className="max-w-2xl text-2xl font-semibold tracking-tight sm:text-3xl">
        Understand an unfamiliar codebase in minutes.
      </h1>

      <p className="mt-2.5 max-w-xl text-sm leading-relaxed text-muted-foreground">
        Point it at a public Python repo. It chunks code on AST boundaries,
        builds a symbol graph, then answers questions with citations you can
        click straight into the source.
      </p>

      <dl className="mt-5 grid gap-2.5 sm:grid-cols-3">
        {STATS.map((stat) => (
          <div
            key={stat.value}
            className="rounded-lg border bg-card px-3 py-2.5 shadow-sm"
          >
            <dt className="font-mono text-base font-semibold tracking-tight">
              {stat.value}
            </dt>
            <dd className="mt-0.5 text-[11px] leading-snug text-muted-foreground">
              {stat.label}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

export default function Home() {
  return (
    <main className="relative">
      <div
        aria-hidden
        className="hero-glow pointer-events-none absolute inset-x-0 top-0 h-[280px]"
      />
      <div className="page-container relative pb-16">
        <Hero />
        <RepoDashboard />
      </div>
    </main>
  );
}
