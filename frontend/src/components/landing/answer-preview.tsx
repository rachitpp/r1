/**
 * A hand-authored still of one answer, shown beside the submit form.
 *
 * It is an illustration, not a captured transcript — hence the caption. It is
 * deliberately built from the same parts the real chat renders (collapsed tool
 * trace, prose, `path:start-end` chips, an excerpt with a highlighted range) so
 * a visitor sees the actual shape of the output before submitting anything.
 *
 * Static and server-rendered: no state, no Shiki. The four tokens below are
 * tinted from the palette by hand, which for six lines of Python is less code
 * than wiring the real highlighter into a decorative panel.
 */

/** Two calls, not three: a third truncates to an ellipsis in the panel's width,
 * and retrieve-then-traverse is the pair that actually shows the graph doing
 * work the retriever could not. */
const TOOL_TRACE = "search_code → get_definition";

const CITATIONS = [
  "_transports/default.py:243-268",
  "_client.py:1012-1024",
] as const;

/** Python keyword / number / dimmed builtin, in palette inks. */
const KW = "text-primary";
const NUM = "text-[hsl(var(--ochre))]";
const DIM = "text-muted-foreground/70";

function Line({
  n,
  mark,
  children,
}: {
  n: number;
  /** The cited line — carries the clay edge marker and a wash, exactly as the
   * code viewer highlights a citation range. */
  mark?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div
      className={
        "relative flex gap-3 whitespace-pre px-3 leading-[1.7]" +
        (mark ? " bg-primary/[0.07]" : "")
      }
    >
      {mark && (
        <span aria-hidden className="absolute inset-y-0 left-0 w-0.5 bg-primary" />
      )}
      <span className="w-6 shrink-0 select-none text-right tabular-nums text-muted-foreground/45">
        {n}
      </span>
      <span>{children}</span>
    </div>
  );
}

export function AnswerPreview() {
  return (
    <figure className="m-0">
      <div className="overflow-hidden rounded-md border bg-card shadow-[0_1px_2px_hsl(25_18%_14%/0.05),0_8px_24px_-12px_hsl(25_18%_14%/0.18)]">
        {/* Window chrome. Three dots and a title, so the panel reads as a
            screenshot of the app rather than as another card on the page. */}
        <div className="flex items-center gap-2 border-b bg-secondary/50 px-3 py-2">
          <span aria-hidden className="flex gap-1.5">
            <span className="size-2 rounded-full bg-border" />
            <span className="size-2 rounded-full bg-border" />
            <span className="size-2 rounded-full bg-border" />
          </span>
          <span className="ml-1 truncate font-mono text-[11px] text-muted-foreground">
            encode/httpx
          </span>
          <span className="ml-auto shrink-0 rounded-sm bg-[hsl(var(--sage)/0.14)] px-1.5 py-px font-mono text-[10px] font-medium text-[hsl(var(--sage))]">
            ready
          </span>
        </div>

        <div className="space-y-3 p-4">
          <p className="text-sm font-medium">
            How does retrying work on a request?
          </p>

          {/* The collapsed tool trace, as it appears once a turn finishes. */}
          <div className="flex items-center gap-2 rounded-sm border bg-secondary/40 px-2.5 py-1.5">
            <span className="shrink-0 text-[11px] font-medium text-muted-foreground">
              2 tool calls
            </span>
            <span className="truncate font-mono text-[11px] text-muted-foreground/80">
              {TOOL_TRACE}
            </span>
            <span className="ml-auto shrink-0 font-mono text-[10px] tabular-nums text-muted-foreground/60">
              4.1s
            </span>
          </div>

          <p className="text-[13px] leading-relaxed text-muted-foreground">
            Retries are a{" "}
            <span className="font-medium text-foreground">transport</span>{" "}
            concern, not a client one.{" "}
            <code className="font-mono text-[12px] text-foreground">
              HTTPTransport
            </code>{" "}
            forwards its <code className="font-mono text-[12px]">retries</code>{" "}
            argument straight into the connection pool, so the client never sees
            a retry happen.
          </p>

          <div className="flex flex-wrap gap-1">
            {CITATIONS.map((c) => (
              <span
                key={c}
                className="rounded border border-border bg-muted/60 px-1.5 py-0.5 font-mono text-[11px] leading-4 text-foreground/80"
              >
                {c}
              </span>
            ))}
          </div>
        </div>

        <div className="border-t bg-secondary/30 py-2.5 font-mono text-[11.5px]">
          <Line n={241}>
            <span className={KW}>class</span> HTTPTransport(BaseTransport):
          </Line>
          <Line n={242}>
            {"    "}
            <span className={KW}>def</span> __init__(
            <span className={DIM}>self</span>, retries:{" "}
            <span className={DIM}>int</span> = <span className={NUM}>0</span>):
          </Line>
          <Line n={243} mark>
            {"        "}
            <span className={DIM}>self</span>._pool = httpcore.ConnectionPool(
          </Line>
          {/* No trailing comment here: the panel clips overflow rather than
              scrolling, and a comment clipped mid-word reads as a bug. */}
          <Line n={244} mark>
            {"            "}retries=retries,
          </Line>
          <Line n={245} mark>
            {"        "})
          </Line>
        </div>
      </div>

      <figcaption className="mt-2 text-[11px] text-muted-foreground">
        Example answer — an illustration of the output shape, not a captured
        transcript.
      </figcaption>
    </figure>
  );
}
