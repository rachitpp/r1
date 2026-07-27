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
        "inline-flex max-w-full items-center truncate rounded border px-1.5 py-0.5 " +
        "font-mono text-[11px] leading-4 transition-colors " +
        (active
          ? "border-amber-400 bg-amber-100 text-amber-900"
          : "border-border bg-muted/60 text-foreground/80 hover:bg-muted")
      }
      title={`View ${formatCitation(citation)}`}
    >
      {formatCitation(citation)}
    </button>
  );
}
