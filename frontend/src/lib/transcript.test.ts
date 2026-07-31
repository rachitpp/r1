import { describe, expect, it } from "vitest";

import type { ChatExchange } from "@/lib/chat-types";
import { toMarkdown, transcriptFilename } from "@/lib/transcript";

const META = {
  repoName: "encode/httpx",
  repoUrl: "https://github.com/encode/httpx",
  headSha: "b5addb64f0161ff6bfe94c124ef76f6a1fba5254",
  exportedAt: new Date("2026-07-31T09:00:00Z"),
};

function exchange(over: Partial<ChatExchange> = {}): ChatExchange {
  return {
    question: "How does httpx pick a transport?",
    steps: [],
    answer: "It is chosen in `_init_transport` [httpx/_client.py:718-738].",
    citations: [
      { file_path: "httpx/_client.py", start_line: 718, end_line: 738 },
    ],
    toolCallsUsed: 3,
    error: null,
    ...over,
  };
}

describe("toMarkdown", () => {
  it("puts the question in a heading and the answer under it", () => {
    const md = toMarkdown([exchange()], META);
    expect(md).toContain("## How does httpx pick a transport?");
    expect(md).toContain("It is chosen in `_init_transport`");
  });

  it("links citations to the pinned commit, not to a branch", () => {
    // The export is only useful outside this app if a citation still resolves
    // there, and it only resolves *correctly* against the commit the snapshot
    // was taken at — a `main` link rots the first time the repo moves.
    const md = toMarkdown([exchange()], META);
    expect(md).toContain(
      "https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/httpx/_client.py#L718-L738",
    );
  });

  it("falls back to a plain citation when there is no commit to link", () => {
    const md = toMarkdown([exchange()], { repoName: "encode/httpx" });
    expect(md).toContain("`httpx/_client.py:718-738`");
    expect(md).not.toContain("https://github.com");
  });

  it("collapses the tool trace into a details block", () => {
    const md = toMarkdown(
      [
        exchange({
          steps: [
            {
              n: 1,
              tool: "search_code",
              args: { query: "transport" },
              summary: "6 hits",
              ms: 1200,
            },
          ],
          toolCallsUsed: 1,
        }),
      ],
      META,
    );
    expect(md).toContain("<summary>Tool trace (1 call)</summary>");
    expect(md).toContain('`search_code(query="transport")` → 6 hits');
    expect(md).toContain("_(1.2s)_");
  });

  it("records an error and a stopped stream rather than dropping them", () => {
    const md = toMarkdown(
      [exchange({ error: "stream ended unexpectedly", answer: "" })],
      META,
    );
    expect(md).toContain("> **Error:** stream ended unexpectedly");

    const stopped = toMarkdown([exchange({ stopped: true })], META);
    expect(stopped).toContain("Stopped early");
  });

  it("handles an empty conversation without emitting a bare header", () => {
    const md = toMarkdown([], META);
    expect(md).toContain("_No questions in this conversation yet._");
  });

  it("names the commit in the header", () => {
    expect(toMarkdown([exchange()], META)).toContain("`b5addb64`");
  });
});

describe("transcriptFilename", () => {
  it("slugifies the repo name and dates the file", () => {
    expect(transcriptFilename("encode/httpx", META.exportedAt)).toBe(
      "encode-httpx-chat-2026-07-31.md",
    );
  });

  it("never produces a leading or trailing dash", () => {
    expect(transcriptFilename("///", META.exportedAt)).toBe(
      "repo-chat-2026-07-31.md",
    );
  });
});
