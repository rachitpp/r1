import { AnswerPreview } from "@/components/landing/answer-preview";
import { RepoList } from "@/components/landing/repo-list";
import { RepoSubmit } from "@/components/landing/repo-submit";

/**
 * Every figure here is measured and lives in README.md ("The comparison").
 * Retrieval is the pipeline's own number on the 20 frozen questions — it is not
 * a claim that AST chunking beats the naive baseline, which it ties at hit@10.
 */
const MEASURES: { value: string; label: string }[] = [
  { value: "0.95", label: "hit@10 · 20 frozen questions" },
  { value: "AST", label: "tree-sitter chunk boundaries" },
  { value: "file:line", label: "clickable citation on every answer" },
];

/**
 * The numbers, as a full-bleed band in the dark palette.
 *
 * `.dark` is applied to the section rather than to `<html>`: the dark tokens
 * were already written and otherwise dormant, and scoping the class here flips
 * every token — border, muted, primary — for this subtree only. The band is the
 * page's one change of key, and it lands on the strongest evidence.
 */
function Measures() {
  return (
    <section className="dark border-y border-border bg-background text-foreground">
      <div className="page-container">
        <dl className="grid gap-y-8 py-12 sm:grid-cols-3 sm:gap-x-10 sm:divide-x sm:divide-border">
          {MEASURES.map((measure, i) => (
            <div key={measure.value} className={i > 0 ? "sm:pl-10" : undefined}>
              <dt className="display text-3xl font-semibold leading-none sm:text-4xl">
                {measure.value}
              </dt>
              <dd className="mt-2.5 text-xs font-medium leading-snug text-muted-foreground">
                {measure.label}
              </dd>
            </div>
          ))}
        </dl>
      </div>
    </section>
  );
}

/**
 * Oversized display type, set to break its own left edge.
 *
 * The outdent is gated at `xl` because that is the first breakpoint where the
 * viewport is reliably wider than `max-w-5xl` plus gutters — below it there is
 * no slack to hang into and the line would clip.
 */
function Hero() {
  return (
    <section className="page-container pb-0 pt-8 sm:pt-10">
      <p className="eyebrow">Public Python repos</p>

      <h1 className="display mt-3 text-[2.1rem] font-semibold leading-[1.03] sm:text-5xl lg:text-6xl">
        Understand
        <br />
        an unfamiliar codebase
        <br />
        <span className="text-primary xl:-ml-8 xl:inline-block">
          in minutes.
        </span>
      </h1>
    </section>
  );
}

export default function Home() {
  return (
    <main className="pb-20">
      <Hero />

      {/* The form and the still sit side by side: the left column says what to
          do, the right shows what comes back. Below `lg` the still stacks under
          the form, where it still reads as an answer to "and then what?". */}
      <section className="page-container -mt-3 pb-10">
        {/* Centred, not top-aligned: the still is much taller than the form, and
            top-aligning left ~400px of dead space under the button. */}
        <div className="grid items-start gap-6 lg:grid-cols-12 lg:items-center lg:gap-10">
          <div className="lg:col-span-7">
            <p className="mb-4 max-w-lg text-[15px] font-medium leading-relaxed text-foreground/80">
              Point it at a repo. It chunks the code on AST boundaries, builds a
              symbol graph, then answers your questions with citations you can
              click straight into the source.
            </p>
            <RepoSubmit />
          </div>

          <div className="lg:col-span-5">
            <AnswerPreview />
          </div>
        </div>
      </section>

      <Measures />

      <section className="page-container pt-14">
        {/* No gate: RepoList renders nothing for a signed-out visitor rather
            than a second sign-in card under the one in the hero. */}
        <RepoList />
      </section>
    </main>
  );
}
