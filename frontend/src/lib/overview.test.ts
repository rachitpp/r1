import { describe, expect, it } from "vitest";

import { parseMarkdown } from "@/lib/markdown";
import { sectionQuestion, splitSections } from "@/lib/overview";

const DOC = `## What this is
An HTTP client [httpx/_client.py:1-9].

## How it is organised
Layered around \`Client\` [httpx/_client.py:60-80].

### A subheading
Still part of the same section.

## Where execution starts
Nothing in the repo reaches [httpx/__main__.py:1-4].`;

describe("splitSections", () => {
  it("groups blocks under their ## heading", () => {
    const sections = splitSections(parseMarkdown(DOC));
    expect(sections.map((s) => s.title)).toEqual([
      "What this is",
      "How it is organised",
      "Where execution starts",
    ]);
  });

  it("keeps a ### inside its parent section", () => {
    // Splitting on every heading level would shatter one section into
    // fragments, each growing its own "ask more" link.
    const [, organised] = splitSections(parseMarkdown(DOC));
    const text = JSON.stringify(organised.blocks);
    expect(text).toContain("A subheading");
    expect(text).toContain("Still part of the same section");
  });

  it("excludes the heading itself from the section body", () => {
    const [first] = splitSections(parseMarkdown(DOC));
    expect(JSON.stringify(first.blocks)).not.toContain("What this is");
  });

  it("keeps a preamble written before the first heading", () => {
    // Silently dropping model output is the worst available handling.
    const sections = splitSections(parseMarkdown("Intro line.\n\n## Real\nBody."));
    expect(sections[0].title).toBeNull();
    expect(JSON.stringify(sections[0].blocks)).toContain("Intro line.");
  });

  it("drops a heading the model left empty", () => {
    const sections = splitSections(parseMarkdown("## Filled\nBody.\n\n## Empty"));
    expect(sections.map((s) => s.title)).toEqual(["Filled"]);
  });

  it("returns nothing for an empty document", () => {
    expect(splitSections(parseMarkdown(""))).toEqual([]);
  });

  it("strips citations out of a heading rather than rendering them raw", () => {
    const [only] = splitSections(
      parseMarkdown("## Entry [httpx/_main.py:1-2]\nBody."),
    );
    expect(only.title).toBe("Entry");
  });
});

describe("sectionQuestion", () => {
  it("turns a label into something answerable", () => {
    expect(sectionQuestion("Where execution starts")).toBe(
      "Where execution starts — walk me through this in more detail, with the code.",
    );
  });

  it("does not double up terminal punctuation", () => {
    expect(sectionQuestion("What this is.")).toContain("What this is —");
  });
});
