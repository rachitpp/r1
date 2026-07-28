import { describe, expect, it } from "vitest";

import { inlineCitationKeys, parseInline, parseMarkdown } from "./markdown";

const text = (s: string) => ({ kind: "text", text: s });

describe("parseInline", () => {
  it("turns **strong**, *em* and `code` into nodes", () => {
    expect(parseInline("a **b** c *d* e `f`")).toEqual([
      text("a "),
      { kind: "strong", children: [text("b")] },
      text(" c "),
      { kind: "em", children: [text("d")] },
      text(" e "),
      { kind: "code", text: "f" },
    ]);
  });

  it("leaves Python dunders alone — `_` is never emphasis", () => {
    expect(parseInline("the __name__ == \"__main__\" block")).toEqual([
      text('the __name__ == "__main__" block'),
    ]);
  });

  it("does not read markdown inside inline code", () => {
    expect(parseInline("`a **b** c`")).toEqual([
      { kind: "code", text: "a **b** c" },
    ]);
  });

  it("renders citation markers as citation nodes", () => {
    expect(parseInline("see [a/b.py:1-9] there")).toEqual([
      text("see "),
      {
        kind: "citation",
        citation: { file_path: "a/b.py", start_line: 1, end_line: 9 },
      },
      text(" there"),
    ]);
  });

  it("rejects malformed citation ranges, keeping the literal text", () => {
    expect(parseInline("[a.py:5-2]")).toEqual([text("[a.py:5-2]")]);
  });

  it("leaves half-written markers literal — every stream frame hits this", () => {
    expect(parseInline("partial **bold and `code")).toEqual([
      text("partial **bold and `code"),
    ]);
  });
});

describe("parseMarkdown", () => {
  it("splits paragraphs on blank lines and keeps inner newlines", () => {
    const blocks = parseMarkdown("one\ntwo\n\nthree");
    expect(blocks).toHaveLength(2);
    expect(blocks[0]).toEqual({
      kind: "paragraph",
      children: [text("one\ntwo")],
    });
  });

  it("groups consecutive bullets into one list", () => {
    const blocks = parseMarkdown("- a\n- b");
    expect(blocks).toEqual([
      { kind: "list", ordered: false, items: [[text("a")], [text("b")]] },
    ]);
  });

  it("keeps ordered and unordered lists separate", () => {
    const blocks = parseMarkdown("1. a\n- b");
    expect(blocks.map((b) => b.kind === "list" && b.ordered)).toEqual([
      true,
      false,
    ]);
  });

  it("does not mistake *emphasis* at line start for a bullet", () => {
    expect(parseMarkdown("*hi* there")[0].kind).toBe("paragraph");
  });

  it("captures fenced code verbatim, with its language", () => {
    expect(parseMarkdown("```python\nx = 1\n```")).toEqual([
      { kind: "code", lang: "python", code: "x = 1" },
    ]);
  });

  it("closes an unterminated fence at end of text", () => {
    expect(parseMarkdown("```\nhalf")).toEqual([
      { kind: "code", lang: null, code: "half" },
    ]);
  });

  it("parses headings by level", () => {
    expect(parseMarkdown("## Title")).toEqual([
      { kind: "heading", level: 2, children: [text("Title")] },
    ]);
  });
});

describe("inlineCitationKeys", () => {
  it("collects markers from prose, lists and nested emphasis", () => {
    const blocks = parseMarkdown(
      "See [a/b.py:1-9].\n\n- also **[c/d.py:2-3]**",
    );
    expect(inlineCitationKeys(blocks)).toEqual(
      new Set(["a/b.py:1-9", "c/d.py:2-3"]),
    );
  });
});
