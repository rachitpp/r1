/**
 * Splitting a generated overview into sections (SPEC §19.5).
 *
 * The model writes four `##` headings. Rendering them as one continuous
 * document works, but the point of the overview is that each section is a door
 * — so the blocks are grouped under their heading here, and the component hangs
 * an "ask more" link off each group.
 *
 * Pure, so the grouping rules are testable without a model or a DOM.
 */

import type { Block, Inline } from "@/lib/markdown";

export interface OverviewSection {
  /** Plain text of the `##` heading, or null for content before the first one. */
  title: string | null;
  /** Everything under it, heading excluded — the heading renders separately. */
  blocks: Block[];
}

/** Flatten inline nodes to their text, dropping formatting and citations. */
function inlineText(nodes: Inline[]): string {
  return nodes
    .map((n) => {
      switch (n.kind) {
        case "text":
        case "code":
          return n.text;
        case "citation":
          return ""; // a heading should not carry a citation; if it does, drop it
        default:
          return inlineText(n.children);
      }
    })
    .join("")
    .trim();
}

/**
 * Group blocks under their `##` headings.
 *
 * Splits on level 2 only. The prompt asks for `##`, and a stray `###` inside a
 * section is content belonging to it rather than a new door — splitting on
 * every heading level would shatter a section into fragments with an "ask more"
 * link on each.
 *
 * A preamble before the first heading keeps `title: null` rather than being
 * dropped: a model that opens with a sentence has still said something, and
 * silently discarding output is the worst way to handle it.
 */
export function splitSections(blocks: Block[]): OverviewSection[] {
  const sections: OverviewSection[] = [];
  let current: OverviewSection | null = null;

  for (const block of blocks) {
    if (block.kind === "heading" && block.level <= 2) {
      current = { title: inlineText(block.children) || null, blocks: [] };
      sections.push(current);
      continue;
    }
    if (!current) {
      current = { title: null, blocks: [] };
      sections.push(current);
    }
    current.blocks.push(block);
  }

  // An empty trailing section — a heading the model never filled in — is noise
  // with a dangling "ask more" link under it.
  return sections.filter((s) => s.blocks.length > 0 || s.title === null);
}

/**
 * The question behind a section's "ask more" link.
 *
 * Phrased as the reader's follow-up rather than as the heading repeated back,
 * because the heading is a label ("Where execution starts") and the question
 * needs to be answerable ("Walk me through where execution starts…").
 */
export function sectionQuestion(title: string): string {
  const topic = title.replace(/[.?!]+$/, "");
  return `${topic} — walk me through this in more detail, with the code.`;
}
