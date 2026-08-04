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

/**
 * A §27 grounding verdict for one citation.
 *
 * Advisory, and deliberately three-valued: `unchecked` means the claim named
 * no code identifiers, so the backend's lexical check had nothing to compare —
 * a blind spot, not a finding. Rendering it as a warning would teach readers to
 * ignore the badge, which costs the one case where it mattered.
 */
export interface Grounding {
  file_path: string;
  start_line: number;
  end_line: number;
  verdict: "supported" | "unsupported" | "unchecked";
  matched: string[];
  missing: string[];
}

/** Verdict for one citation, by key. Absent when grounding did not run. */
export function groundingFor(
  grounding: Grounding[] | undefined,
  citation: Citation,
): Grounding | undefined {
  if (!grounding) return undefined;
  return grounding.find(
    (g) =>
      g.file_path === citation.file_path &&
      g.start_line === citation.start_line &&
      g.end_line === citation.end_line,
  );
}

/**
 * Mirror of the backend's CITATION_RE (app/agent/citations.py).
 *
 * Widened for SPEC §30: the corpus holds documentation, manifests and CI config
 * as well as code, and a `[README.md:90-104]` the backend validated must render
 * as a chip here rather than as literal text. The backend builds its pattern
 * from the §12 selection constants; this is the hand-kept mirror, so the two
 * have to be changed together — the backend's `parse_citations` is the
 * authority, and anything it drops never reaches this file.
 */
export const CITATION_RE =
  /\[((?:[\w./-]*(?:\.py|\.md|\.rst|\.txt|\.toml|\.yaml|\.yml|\.cfg|\.ini))|(?:[\w./-]*(?:Dockerfile|Makefile|Pipfile)[\w.-]*)):(\d+)-(\d+)\]/g;

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

/**
 * The templated question behind the viewer's "Explain" button.
 *
 * Phrased as a `[path:start-end]` marker because that is the citation syntax
 * the agent's own prompt uses (SPEC §7.5) — the model reads it as a location it
 * already knows how to resolve, rather than as prose it has to search for. The
 * three clauses map onto what the graph can actually answer: the definition,
 * its callers (`find_references`), and its callees (`expand_context`).
 */
export function explainQuestion(c: Citation): string {
  return (
    `Explain [${citationKey(c)}]: what does this code do, ` +
    `what calls it, and what does it depend on?`
  );
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
