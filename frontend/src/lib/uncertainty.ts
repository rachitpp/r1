/**
 * The `[uncertain: …]` marker (SPEC §25, FEATURE-IDEAS 5.4).
 *
 * The agent may end an answer with one marker saying what it could not
 * confirm. Left as prose it reads like a stray bracket; parsed, it becomes a
 * callout the reader can weigh — which is the entire point of asking for it in
 * a fixed shape rather than inviting the model to hedge in words.
 *
 * Pure, and split out of `markdown.ts` because the marker is not markdown: it
 * is a contract with the prompt, and it deserves to fail loudly on its own
 * tests rather than inside the renderer's.
 */

/**
 * Matches only a marker that ends the answer, which is where §25 puts it.
 *
 * Anchored on purpose. A model quoting the instruction back — or an answer
 * *about* this feature, which is not hypothetical in a codebase-Q&A tool
 * pointed at its own repo — would otherwise have its prose silently eaten.
 */
const TRAILING = /\n*\[uncertain:\s*([^\]]*)\]\s*$/i;

export interface ParsedAnswer {
  /** The answer with the marker removed, ready to render as markdown. */
  body: string;
  /** What the model said it could not confirm, or null. */
  uncertainty: string | null;
}

export function parseUncertainty(answer: string): ParsedAnswer {
  const match = TRAILING.exec(answer);
  if (!match) return { body: answer, uncertainty: null };

  const reason = match[1].trim().replace(/\s+/g, " ");
  const body = answer.slice(0, match.index).trimEnd();

  // An empty marker is a marker the model failed to fill in. Dropping it beats
  // rendering a callout that says nothing, which would read as a UI bug.
  if (!reason) return { body, uncertainty: null };

  return { body, uncertainty: reason };
}
