/**
 * Citations: `[path:start-end]` markers (SPEC §7.5) and §9 location objects.
 *
 * The backend already validates the `citations` event against the files table,
 * so those are trusted. Markers parsed out of answer *text* are only used for
 * inline chip rendering — the mirror of the backend's CITATION_RE, path
 * restricted to `.py` exactly as §7.5 does.
 */

export interface Citation {
  file_path: string;
  start_line: number;
  end_line: number;
}

/** Mirror of the backend's CITATION_RE (app/agent/citations.py). */
export const CITATION_RE = /\[([\w./-]+\.py):(\d+)-(\d+)\]/g;

/** Parse `[path:start-end]` markers, deduped, in order of appearance. */
export function parseCitations(text: string): Citation[] {
  const out: Citation[] = [];
  const seen = new Set<string>();
  for (const m of text.matchAll(CITATION_RE)) {
    const citation: Citation = {
      file_path: m[1],
      start_line: Number(m[2]),
      end_line: Number(m[3]),
    };
    if (citation.start_line < 1 || citation.end_line < citation.start_line)
      continue; // malformed range — not a citation
    const key = citationKey(citation);
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(citation);
  }
  return out;
}

export function citationKey(c: Citation): string {
  return `${c.file_path}:${c.start_line}-${c.end_line}`;
}

/** `httpx/_config.py:120-145` — the display form used on chips. */
export function formatCitation(c: Citation): string {
  return citationKey(c);
}

/** Merge citation lists (event citations first), deduped by key. */
export function dedupeCitations(...lists: Citation[][]): Citation[] {
  const out: Citation[] = [];
  const seen = new Set<string>();
  for (const list of lists) {
    for (const c of list) {
      const key = citationKey(c);
      if (seen.has(key)) continue;
      seen.add(key);
      out.push(c);
    }
  }
  return out;
}

/**
 * Split answer text into plain segments and citation markers, for rendering
 * the markers as inline chips instead of raw `[path:1-2]` noise.
 */
export type AnswerSegment =
  | { kind: "text"; text: string }
  | { kind: "citation"; citation: Citation };

export function segmentAnswer(text: string): AnswerSegment[] {
  const segments: AnswerSegment[] = [];
  let last = 0;
  for (const m of text.matchAll(CITATION_RE)) {
    const start = m.index ?? 0;
    const citation: Citation = {
      file_path: m[1],
      start_line: Number(m[2]),
      end_line: Number(m[3]),
    };
    if (citation.start_line < 1 || citation.end_line < citation.start_line)
      continue;
    if (start > last) segments.push({ kind: "text", text: text.slice(last, start) });
    segments.push({ kind: "citation", citation });
    last = start + m[0].length;
  }
  if (last < text.length) segments.push({ kind: "text", text: text.slice(last) });
  return segments;
}
