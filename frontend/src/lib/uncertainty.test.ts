import { describe, expect, it } from "vitest";

import { parseUncertainty } from "@/lib/uncertainty";

describe("parseUncertainty", () => {
  it("extracts a trailing marker and strips it from the prose", () => {
    const { body, uncertainty } = parseUncertainty(
      "The retry count comes from the caller.\n\n[uncertain: the default is set outside this repo]",
    );
    expect(body).toBe("The retry count comes from the caller.");
    expect(uncertainty).toBe("the default is set outside this repo");
  });

  it("leaves a confident answer completely untouched", () => {
    const answer = "Tokens are verified in [pkg/auth.py:1-2].";
    expect(parseUncertainty(answer)).toEqual({ body: answer, uncertainty: null });
  });

  it("ignores a marker that is not at the end", () => {
    // An answer *about* this feature is not hypothetical in a tool pointed at
    // its own repo — quoting the syntax must not eat the rest of the prose.
    const answer =
      "The prompt asks for [uncertain: reason] at the end.\n\nThat is §25.";
    expect(parseUncertainty(answer).uncertainty).toBeNull();
    expect(parseUncertainty(answer).body).toBe(answer);
  });

  it("collapses a marker that the model hard-wrapped", () => {
    const { uncertainty } = parseUncertainty(
      "Answer.\n[uncertain: the default is set by\n   whoever constructs it]",
    );
    expect(uncertainty).toBe("the default is set by whoever constructs it");
  });

  it("drops an empty marker rather than rendering a blank callout", () => {
    const { body, uncertainty } = parseUncertainty("Answer.\n\n[uncertain: ]");
    expect(uncertainty).toBeNull();
    expect(body).toBe("Answer.");
  });

  it("is case-insensitive about the keyword", () => {
    expect(parseUncertainty("A.\n[Uncertain: x]").uncertainty).toBe("x");
  });

  it("tolerates trailing whitespace after the marker", () => {
    expect(parseUncertainty("A.\n[uncertain: x]\n\n  ").uncertainty).toBe("x");
  });

  it("handles an answer that is only a marker", () => {
    const { body, uncertainty } = parseUncertainty("[uncertain: nothing found]");
    expect(body).toBe("");
    expect(uncertainty).toBe("nothing found");
  });
});
