import { describe, expect, it } from "vitest";

import { type Grounding, groundingFor } from "@/lib/citations";

const CITE = { file_path: "pkg/a.py", start_line: 1, end_line: 2 };

const VERDICTS: Grounding[] = [
  {
    ...CITE,
    verdict: "unsupported",
    matched: [],
    missing: ["make_id"],
  },
  {
    file_path: "pkg/b.py",
    start_line: 5,
    end_line: 9,
    verdict: "supported",
    matched: ["connect"],
    missing: [],
  },
];

describe("groundingFor", () => {
  it("matches on the whole range, not just the path", () => {
    expect(groundingFor(VERDICTS, CITE)?.verdict).toBe("unsupported");
    expect(
      groundingFor(VERDICTS, { ...CITE, start_line: 99, end_line: 100 }),
    ).toBeUndefined();
  });

  it("returns undefined when grounding did not run", () => {
    // The backend degrades to an empty array rather than failing the stream,
    // so the UI must cope with the signal simply being absent.
    expect(groundingFor(undefined, CITE)).toBeUndefined();
    expect(groundingFor([], CITE)).toBeUndefined();
  });

  it("finds a supported verdict for a different citation", () => {
    const found = groundingFor(VERDICTS, {
      file_path: "pkg/b.py",
      start_line: 5,
      end_line: 9,
    });
    expect(found?.verdict).toBe("supported");
    expect(found?.matched).toEqual(["connect"]);
  });
});
