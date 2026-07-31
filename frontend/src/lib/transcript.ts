/**
 * Export a conversation as Markdown.
 *
 * The point is that the export is *useful away from this app*: onboarding notes
 * pasted into a PR, an issue, or a team doc. So citations become GitHub blob
 * links when the repo and commit are known — a bare `httpx/_client.py:718-738`
 * is only meaningful to someone with this page open, and the whole reason it
 * can be linked is that a snapshot is pinned to a commit (SPEC §14.3).
 *
 * Pure functions, no DOM: the download wrapper is the only part that touches
 * the browser, which is what makes the formatting testable.
 */

import type { ChatExchange } from "@/lib/chat-types";
import { type Citation, citationKey } from "@/lib/citations";
import { githubBlobUrl } from "@/lib/format";

export interface TranscriptMeta {
  /** `owner/name`, already stripped of any strategy suffix. */
  repoName: string;
  repoUrl?: string;
  headSha?: string | null;
  /** Injected rather than read from the clock, so the output is testable. */
  exportedAt?: Date;
}

function citationLine(c: Citation, meta: TranscriptMeta): string {
  const label = citationKey(c);
  const href = githubBlobUrl(meta.repoUrl, meta.headSha, c);
  return href ? `- [\`${label}\`](${href})` : `- \`${label}\``;
}

/** `search_code({"query": "auth"})` — the step line in the tool trace. */
function stepLine(
  step: ChatExchange["steps"][number],
  index: number,
): string {
  const args = Object.entries(step.args)
    .map(([k, v]) => `${k}=${JSON.stringify(v)}`)
    .join(", ");
  const timing = step.ms == null ? "" : ` _(${(step.ms / 1000).toFixed(1)}s)_`;
  const summary = step.summary ? ` → ${step.summary}` : "";
  return `${index}. \`${step.tool}(${args})\`${summary}${timing}`;
}

export function toMarkdown(
  transcript: ChatExchange[],
  meta: TranscriptMeta,
): string {
  const stamp = (meta.exportedAt ?? new Date()).toISOString().slice(0, 10);
  const lines: string[] = [
    `# ${meta.repoName} — codebase Q&A`,
    "",
    meta.headSha
      ? `Indexed at commit \`${meta.headSha.slice(0, 8)}\` · exported ${stamp}`
      : `Exported ${stamp}`,
    "",
  ];

  if (transcript.length === 0) {
    lines.push("_No questions in this conversation yet._", "");
    return lines.join("\n");
  }

  for (const exchange of transcript) {
    lines.push(`## ${exchange.question}`, "");

    if (exchange.error) {
      lines.push(`> **Error:** ${exchange.error}`, "");
    }
    if (exchange.stopped) {
      lines.push("> _Stopped early; the answer below is partial._", "");
    }
    if (exchange.answer.trim()) {
      lines.push(exchange.answer.trim(), "");
    }

    if (exchange.citations.length > 0) {
      lines.push("**Citations**", "");
      for (const c of exchange.citations) lines.push(citationLine(c, meta));
      lines.push("");
    }

    if (exchange.steps.length > 0) {
      // Collapsed: the trace is what makes an answer auditable, but it is three
      // times the length of the answer and nobody reading the note wants it
      // open by default. <details> renders natively on GitHub.
      const used =
        exchange.toolCallsUsed ?? exchange.steps.length;
      lines.push(
        "<details>",
        `<summary>Tool trace (${used} call${used === 1 ? "" : "s"})</summary>`,
        "",
      );
      exchange.steps.forEach((step, i) => lines.push(stepLine(step, i + 1)));
      lines.push("", "</details>", "");
    }
  }

  return lines.join("\n");
}

/** `httpx-chat-2026-07-31.md` — safe on every filesystem we care about. */
export function transcriptFilename(repoName: string, at?: Date): string {
  const slug =
    repoName
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-|-$/g, "") || "repo";
  return `${slug}-chat-${(at ?? new Date()).toISOString().slice(0, 10)}.md`;
}

/** Trigger a download of `text`. The only part of this module that needs a DOM. */
export function downloadMarkdown(filename: string, text: string): void {
  const url = URL.createObjectURL(
    new Blob([text], { type: "text/markdown;charset=utf-8" }),
  );
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  // Revoking immediately is safe: the click has already handed the blob to the
  // download manager, and not revoking leaks the whole transcript per export.
  URL.revokeObjectURL(url);
}
