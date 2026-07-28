/**
 * A deliberately small markdown parser for agent answers.
 *
 * Why not a library: the answer stream is interleaved with `[path:start-end]`
 * citation markers (SPEC §7.5) that must become interactive chips *inside* the
 * prose, and it arrives token by token — so every intermediate string is
 * half-written markdown. This parser leaves anything unmatched as literal text,
 * which is exactly the degradation a streaming renderer needs.
 *
 * Supported: fenced code, headings, ordered/unordered lists, `inline code`,
 * **strong**, *emphasis*, and citation markers.
 *
 * Deliberately NOT supported: `_underscore_` emphasis. Python identifiers are
 * full of underscores — `__name__` would render as emphasised "name" — and the
 * cost of that false positive in a code assistant is far higher than the cost
 * of showing a rare literal underscore pair.
 */

import { type Citation } from "@/lib/citations";

export type Inline =
  | { kind: "text"; text: string }
  | { kind: "code"; text: string }
  | { kind: "strong"; children: Inline[] }
  | { kind: "em"; children: Inline[] }
  | { kind: "citation"; citation: Citation };

export type Block =
  | { kind: "paragraph"; children: Inline[] }
  | { kind: "heading"; level: number; children: Inline[] }
  | { kind: "list"; ordered: boolean; items: Inline[][] }
  | { kind: "code"; lang: string | null; code: string };

/**
 * One pass, four alternatives, tried left to right at each position — which is
 * why `**` precedes `*`. A fresh regex per call keeps `lastIndex` local.
 */
function inlineMatcher(): RegExp {
  return /`([^`\n]+)`|\[([\w./-]+\.py):(\d+)-(\d+)\]|\*\*([^*]+)\*\*|\*([^*\n]+)\*/g;
}

const MAX_INLINE_DEPTH = 3;

export function parseInline(text: string, depth = 0): Inline[] {
  const out: Inline[] = [];
  if (!text) return out;
  if (depth >= MAX_INLINE_DEPTH) return [{ kind: "text", text }];

  const re = inlineMatcher();
  let last = 0;
  let m: RegExpExecArray | null;

  const pushText = (slice: string) => {
    if (slice) out.push({ kind: "text", text: slice });
  };

  while ((m = re.exec(text)) !== null) {
    const [whole, code, path, start, end, strong, em] = m;
    const at = m.index;

    if (path !== undefined) {
      const citation: Citation = {
        file_path: path,
        start_line: Number(start),
        end_line: Number(end),
      };
      // Malformed ranges are not citations — leave the literal text alone.
      if (citation.start_line < 1 || citation.end_line < citation.start_line) {
        continue;
      }
      pushText(text.slice(last, at));
      out.push({ kind: "citation", citation });
    } else if (code !== undefined) {
      pushText(text.slice(last, at));
      out.push({ kind: "code", text: code });
    } else if (strong !== undefined) {
      pushText(text.slice(last, at));
      out.push({ kind: "strong", children: parseInline(strong, depth + 1) });
    } else if (em !== undefined) {
      pushText(text.slice(last, at));
      out.push({ kind: "em", children: parseInline(em, depth + 1) });
    } else {
      continue;
    }
    last = at + whole.length;
  }

  pushText(text.slice(last));
  return out;
}

const FENCE = /^\s*```(.*)$/;
const HEADING = /^(#{1,6})\s+(.*)$/;
const UL_ITEM = /^\s{0,3}[-*+]\s+(.*)$/;
const OL_ITEM = /^\s{0,3}\d+[.)]\s+(.*)$/;

export function parseMarkdown(text: string): Block[] {
  const blocks: Block[] = [];
  const lines = text.split("\n");

  let paragraph: string[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;

  const flushParagraph = () => {
    if (paragraph.length === 0) return;
    // Joined with "\n" and rendered pre-wrap: intra-paragraph line breaks the
    // model chose to emit are meaningful and are kept.
    blocks.push({ kind: "paragraph", children: parseInline(paragraph.join("\n")) });
    paragraph = [];
  };
  const flushList = () => {
    if (!list) return;
    blocks.push({
      kind: "list",
      ordered: list.ordered,
      items: list.items.map((item) => parseInline(item)),
    });
    list = null;
  };
  const flushAll = () => {
    flushParagraph();
    flushList();
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    const fence = FENCE.exec(line);
    if (fence) {
      flushAll();
      const lang = fence[1].trim() || null;
      const body: string[] = [];
      i++;
      // An unterminated fence is the normal mid-stream state, so running to the
      // end of the text is a valid close, not an error.
      while (i < lines.length && !FENCE.test(lines[i])) {
        body.push(lines[i]);
        i++;
      }
      blocks.push({ kind: "code", lang, code: body.join("\n") });
      continue;
    }

    if (line.trim() === "") {
      flushAll();
      continue;
    }

    const heading = HEADING.exec(line);
    if (heading) {
      flushAll();
      blocks.push({
        kind: "heading",
        level: heading[1].length,
        children: parseInline(heading[2]),
      });
      continue;
    }

    const ol = OL_ITEM.exec(line);
    const ul = ol ? null : UL_ITEM.exec(line);
    if (ol || ul) {
      flushParagraph();
      const ordered = ol != null;
      const content = (ol ?? ul)![1];
      if (list && list.ordered !== ordered) flushList();
      list ??= { ordered, items: [] };
      list.items.push(content);
      continue;
    }

    flushList();
    paragraph.push(line);
  }

  flushAll();
  return blocks;
}

/** Citation markers reachable from parsed prose — used to avoid repeating a
 * chip in the Sources strip that already renders inline in the answer. */
export function inlineCitationKeys(blocks: Block[]): Set<string> {
  const keys = new Set<string>();
  const walk = (nodes: Inline[]) => {
    for (const node of nodes) {
      if (node.kind === "citation") {
        const c = node.citation;
        keys.add(`${c.file_path}:${c.start_line}-${c.end_line}`);
      } else if (node.kind === "strong" || node.kind === "em") {
        walk(node.children);
      }
    }
  };
  for (const block of blocks) {
    if (block.kind === "paragraph" || block.kind === "heading") walk(block.children);
    else if (block.kind === "list") block.items.forEach(walk);
  }
  return keys;
}
