"use client";

import { type Citation, formatCitation } from "@/lib/citations";

/**
 * The clickable `path:start-end` chip used for answer citations and step
 * locations. Clicking loads the range into the code viewer — chips never carry
 * code themselves (§9).
 */
export function CitationChip({
  citation,
  onClick,
  active,
}: {
  citation: Citation;
  onClick: (c: Citation) => void;
  active?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={() => onClick(citation)}
      className={
        "inline-flex max-w-full items-center truncate rounded-sm border px-1.5 py-0.5 " +
        "font-mono text-[11px] leading-4 transition-colors " +
        // Active is ochre, the palette's "you are here" ink — the stock amber
        // this used to carry was the last Tailwind default left in the app.
        (active
          ? "border-[hsl(var(--ochre)/0.5)] bg-[hsl(var(--ochre)/0.14)] text-[hsl(var(--ochre))]"
          : "border-border bg-muted/60 text-foreground/80 hover:border-primary/30 hover:bg-muted")
      }
      title={`View ${formatCitation(citation)}`}
    >
      {formatCitation(citation)}
    </button>
  );
}
