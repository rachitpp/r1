import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AnswerBody } from "@/components/answer/answer-body";
import { citationKey } from "@/lib/citations";
import { parseMarkdown } from "@/lib/markdown";

/** An answer shaped like the ones the agent actually streams (SPEC §7.5). */
const ANSWER = [
  "The transport is chosen in [httpx/_client.py:718-738].",
  "",
  "- mounts are consulted first [httpx/_client.py:740-751]",
  "- the default transport is the fallback",
].join("\n");

const blocks = () => parseMarkdown(ANSWER);

describe("AnswerBody", () => {
  it("renders citation markers as chips rather than raw brackets", () => {
    render(
      <AnswerBody blocks={blocks()} onCiteClick={() => {}} activeKey={null} />,
    );

    expect(screen.getAllByRole("button").map((b) => b.textContent)).toEqual([
      "httpx/_client.py:718-738",
      "httpx/_client.py:740-751",
    ]);
    // The literal `[path:a-b]` syntax is an instruction to the renderer, not
    // something a reader should ever see.
    expect(document.body.textContent).not.toContain("[httpx");
  });

  it("reports which citation was clicked", () => {
    const onCiteClick = vi.fn();
    render(
      <AnswerBody blocks={blocks()} onCiteClick={onCiteClick} activeKey={null} />,
    );

    fireEvent.click(screen.getAllByRole("button")[1]);
    expect(onCiteClick).toHaveBeenCalledWith({
      file_path: "httpx/_client.py",
      start_line: 740,
      end_line: 751,
    });
  });

  it("activates the chip whose key matches, and only that one", () => {
    const active = citationKey({
      file_path: "httpx/_client.py",
      start_line: 740,
      end_line: 751,
    });
    render(
      <AnswerBody blocks={blocks()} onCiteClick={() => {}} activeKey={active} />,
    );

    const [first, second] = screen.getAllByRole("button");
    expect(first.className).not.toContain("ochre");
    expect(second.className).toContain("ochre");
  });

  it("keeps list structure instead of flattening to paragraphs", () => {
    const { container } = render(
      <AnswerBody blocks={blocks()} onCiteClick={() => {}} activeKey={null} />,
    );
    expect(container.querySelectorAll("ul li")).toHaveLength(2);
  });
});
