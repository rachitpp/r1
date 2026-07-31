import { describe, expect, it } from "vitest";

import type { ModuleEdge } from "@/lib/api";
import {
  dependenciesOf,
  dependentsOf,
  linkLabel,
  shareOf,
  splitModulePath,
} from "@/lib/architecture";

const EDGES: ModuleEdge[] = [
  { from_path: "a.py", to_path: "core.py", kind: "calls", weight: 4 },
  { from_path: "a.py", to_path: "core.py", kind: "imports", weight: 1 },
  { from_path: "a.py", to_path: "util.py", kind: "imports", weight: 2 },
  { from_path: "b.py", to_path: "core.py", kind: "calls", weight: 9 },
];

describe("dependenciesOf", () => {
  it("merges every edge kind between the same pair", () => {
    const [core, util] = dependenciesOf(EDGES, "a.py");
    // calls(4) + imports(1) between a.py and core.py is one link of weight 5,
    // not two rows — the flat SQL shape is an implementation detail.
    expect(core).toMatchObject({ path: "core.py", weight: 5 });
    expect(core.kinds).toEqual([
      { kind: "calls", weight: 4 },
      { kind: "imports", weight: 1 },
    ]);
    expect(util).toMatchObject({ path: "util.py", weight: 2 });
  });

  it("orders by total weight, heaviest coupling first", () => {
    expect(dependenciesOf(EDGES, "a.py").map((l) => l.path)).toEqual([
      "core.py",
      "util.py",
    ]);
  });

  it("returns nothing for a module with no outgoing edges", () => {
    expect(dependenciesOf(EDGES, "core.py")).toEqual([]);
  });
});

describe("dependentsOf", () => {
  it("pivots the other way and keeps both dependents apart", () => {
    expect(dependentsOf(EDGES, "core.py")).toEqual([
      { path: "b.py", kinds: [{ kind: "calls", weight: 9 }], weight: 9 },
      {
        path: "a.py",
        kinds: [
          { kind: "calls", weight: 4 },
          { kind: "imports", weight: 1 },
        ],
        weight: 5,
      },
    ]);
  });

  it("breaks weight ties on path, so the order does not follow row order", () => {
    const tied: ModuleEdge[] = [
      { from_path: "z.py", to_path: "x.py", kind: "calls", weight: 3 },
      { from_path: "a.py", to_path: "x.py", kind: "calls", weight: 3 },
    ];
    expect(dependentsOf(tied, "x.py").map((l) => l.path)).toEqual([
      "a.py",
      "z.py",
    ]);
  });
});

describe("linkLabel", () => {
  it("spells the kinds out, heaviest first", () => {
    const [core] = dependenciesOf(EDGES, "a.py");
    // Not "ci 5" — first-letter abbreviation saved a few pixels and made the
    // number unreadable, which is the whole information the column carries.
    expect(linkLabel(core)).toBe("4 calls · 1 import");
  });

  it("singularises a weight of one — `edges.kind` is plural in the schema", () => {
    expect(
      linkLabel({ path: "x.py", kinds: [{ kind: "calls", weight: 1 }], weight: 1 }),
    ).toBe("1 call");
    // `extends` is already both, and must not become "extend".
    expect(
      linkLabel({ path: "x.py", kinds: [{ kind: "extends", weight: 1 }], weight: 1 }),
    ).toBe("1 extends");
  });

  it("handles a single kind without a separator", () => {
    const [b] = dependentsOf(EDGES, "core.py");
    expect(linkLabel(b)).toBe("9 calls");
  });

  it("falls back to the bare weight if a link somehow has no kinds", () => {
    expect(linkLabel({ path: "x.py", kinds: [], weight: 3 })).toBe("3");
  });
});

describe("shareOf", () => {
  it("is a clamped fraction", () => {
    expect(shareOf(5, 10)).toBe(0.5);
    expect(shareOf(20, 10)).toBe(1);
    expect(shareOf(-1, 10)).toBe(0);
  });

  it("returns 0 rather than NaN when nothing has any fan-in", () => {
    // Real case, not an error: a repo whose cross-module edges all failed to
    // resolve renders flat bars, never `NaN%`.
    expect(shareOf(0, 0)).toBe(0);
  });
});

describe("splitModulePath", () => {
  it("separates directory from filename", () => {
    expect(splitModulePath("httpx/_client.py")).toEqual({
      dir: "httpx/",
      file: "_client.py",
    });
  });

  it("handles a top-level module", () => {
    expect(splitModulePath("setup.py")).toEqual({ dir: "", file: "setup.py" });
  });
});
