import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { CitationChip } from "@/components/answer/citation-chip";
import type { Citation } from "@/lib/citations";

const CITE: Citation = {
  file_path: "httpx/_client.py",
  start_line: 718,
  end_line: 738,
};

describe("CitationChip", () => {
  it("renders the path:start-end display form", () => {
    render(<CitationChip citation={CITE} onClick={() => {}} />);
    expect(screen.getByRole("button").textContent).toBe(
      "httpx/_client.py:718-738",
    );
  });

  it("hands back the whole citation, not just its key", () => {
    // The viewer needs the line range to scroll and wash; a key would make the
    // caller re-parse a string it already had structured.
    const onClick = vi.fn();
    render(<CitationChip citation={CITE} onClick={onClick} />);
    fireEvent.click(screen.getByRole("button"));
    expect(onClick).toHaveBeenCalledWith(CITE);
  });

  it("marks only the active chip", () => {
    const { rerender } = render(
      <CitationChip citation={CITE} onClick={() => {}} />,
    );
    expect(screen.getByRole("button").className).not.toContain("ochre");

    rerender(<CitationChip citation={CITE} onClick={() => {}} active />);
    expect(screen.getByRole("button").className).toContain("ochre");
  });

  it("carries the range in its title, so a truncated chip is still readable", () => {
    render(<CitationChip citation={CITE} onClick={() => {}} />);
    expect(screen.getByRole("button").getAttribute("title")).toBe(
      "View httpx/_client.py:718-738",
    );
  });
});
