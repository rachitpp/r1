import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StatusBadge } from "@/components/status-badge";

describe("StatusBadge", () => {
  it("labels the §10 state it was given", () => {
    render(<StatusBadge status="ready" />);
    expect(screen.getByText("ready")).toBeTruthy();
  });

  it("pulses only while a worker owns the job", () => {
    // The dot is the whole signal that something is still happening; `ready`
    // and `failed` are terminal and must not animate, or the page reads as
    // busy forever.
    const { container, rerender } = render(<StatusBadge status="embedding" />);
    expect(container.querySelector(".animate-pulse")).not.toBeNull();

    rerender(<StatusBadge status="ready" />);
    expect(container.querySelector(".animate-pulse")).toBeNull();

    rerender(<StatusBadge status="failed" />);
    expect(container.querySelector(".animate-pulse")).toBeNull();
  });

  it("gives the two terminal states different inks", () => {
    const { container: ready } = render(<StatusBadge status="ready" />);
    const { container: failed } = render(<StatusBadge status="failed" />);
    expect(ready.firstElementChild?.className).not.toBe(
      failed.firstElementChild?.className,
    );
  });
});
