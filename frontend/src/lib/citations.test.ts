import { describe, expect, it } from "vitest";

import {
  dedupeCitations,
  formatCitation,
  parseCitations,
  segmentAnswer,
} from "./citations";

describe("parseCitations", () => {
  it("extracts path and 1-based inclusive range", () => {
    expect(parseCitations("See [httpx/_config.py:120-145] for detail.")).toEqual([
      { file_path: "httpx/_config.py", start_line: 120, end_line: 145 },
    ]);
  });

  it("dedupes repeated markers, keeping first-appearance order", () => {
    const text =
      "[a/b.py:1-2] then [c/d.py:3-4] and again [a/b.py:1-2].";
    expect(parseCitations(text).map((c) => c.file_path)).toEqual([
      "a/b.py",
      "c/d.py",
    ]);
  });

  it("rejects malformed ranges, and paths the corpus never holds", () => {
    // `.md` used to belong here. SPEC §30 puts documentation in the corpus, so
    // a README citation is now correct; what stays rejected is a class
    // selection never stores, where a citation could only be fabricated.
    expect(parseCitations("[a.py:5-2] [b.py:0-3] [c.svg:1-2]")).toEqual([]);
  });

  it("accepts the §30 prose and config classes", () => {
    expect(
      parseCitations(
        "[README.md:1-2] [docs/i.rst:3-4] [pyproject.toml:5-6] " +
          "[.github/workflows/ci.yml:7-8] [Dockerfile:9-10]",
      ).map((c) => c.file_path),
    ).toEqual([
      "README.md",
      "docs/i.rst",
      "pyproject.toml",
      ".github/workflows/ci.yml",
      "Dockerfile",
    ]);
  });
});

describe("segmentAnswer", () => {
  it("splits text around markers so chips can render inline", () => {
    const segments = segmentAnswer("Starts in [a/b.py:1-9], ends here.");
    expect(segments).toEqual([
      { kind: "text", text: "Starts in " },
      {
        kind: "citation",
        citation: { file_path: "a/b.py", start_line: 1, end_line: 9 },
      },
      { kind: "text", text: ", ends here." },
    ]);
  });

  it("returns one text segment when there are no markers", () => {
    expect(segmentAnswer("no citations here")).toEqual([
      { kind: "text", text: "no citations here" },
    ]);
  });
});

describe("dedupeCitations", () => {
  it("merges lists with the first list winning order", () => {
    const fromEvent = [{ file_path: "a.py", start_line: 1, end_line: 2 }];
    const fromText = [
      { file_path: "a.py", start_line: 1, end_line: 2 },
      { file_path: "b.py", start_line: 3, end_line: 4 },
    ];
    expect(dedupeCitations(fromEvent, fromText)).toEqual([
      { file_path: "a.py", start_line: 1, end_line: 2 },
      { file_path: "b.py", start_line: 3, end_line: 4 },
    ]);
  });
});

describe("formatCitation", () => {
  it("renders the chip label", () => {
    expect(
      formatCitation({ file_path: "x/y.py", start_line: 12, end_line: 48 }),
    ).toBe("x/y.py:12-48");
  });
});
